# -*- coding: utf-8 -*-
# 豆包 App 执行器：经 winapp ui（UIA）驱动豆包桌面端，把它当成免费/会员额度的 LLM 执行通道
#
# 实验结论（2026-10-16 已验证）：
#   - 豆包 Electron 的 Chromium UIA 树默认全暴露，inspect/search/get-value/invoke 均可后台
#   - 输入框是 contenteditable，set-value 不可用；打字必须 send-keys --via send-input（需前台几秒）
#   - slug 会随 UI 重渲染失效，每步用「文本选择器 / 稳定 DOM 名」重定位，不缓存 slug
#   - 回复完成信号：全树文本 diff，回复连续 3 轮稳定（send 按钮在输入框清空后消失，不能当信号）
#   - 新会话后必须等欢迎页渲染完再采 baseline，否则推荐 chips 会被误判成回复
#   - 发送必须确认输入框清空（invoke 偶尔被吞，需补回车重试）
#
# 用法：
#   代码：  from doubao_executor import ask; print(ask("你好"))
#   CLI：   python doubao_executor.py "用一句话介绍你自己"
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

DEBUG = os.environ.get("DOUBAO_DEBUG")       # 指向文件路径时，wait_reply 每轮快照写入该文件

WINAPP = shutil.which("winapp") or str(
    Path.home() / r"AppData\Local\Microsoft\WindowsApps\winapp.exe")
DOUBAO_EXE = Path.home() / r"AppData\Local\Doubao\Application\app\Doubao.exe"
APP = "Doubao"

INPUT_PLACEHOLDER = "发消息或按住空格说话"
SEND_BUTTON = "flow-end-msg-send"          # 豆包 DOM 原生名（稳定）
INSPECT_DEPTH = "30"                       # 输入框/回复在 web 内容深层，depth 14 到不了
HEADER_ZONE = 170                          # 顶部栏（窗口相对 y）：会话标题区，与回复同文会重复，剔除
POLL_INTERVAL = 3.0
SETTLE_ROUNDS = 3                          # 回复文本连续稳定轮数即判定完成（约 9s 无新字）

# 会话区里不是回复的固定文本（按钮/占位/侧栏），按前缀过滤
NOISE_PREFIXES = (
    "发消息或按住空格说话", "新工作任务", "新对话", "对话列表设置", "归档对话",
    "API 服务", "新建项目", "最小化", "恢复", "关闭", "搜索", "后退", "前进",
    "豆包 快速", "AI 表格", "更多", "最近", "项目", "豆包", "↵",
    "AI 生成可能有误", "相关视频", "相关推荐",
)
# 这些 className 的子树整块跳过（猜你想问 chips / 视频卡）
_CARD_CLASSES = ("suggest-list-item", "video-card", "video-wrapper")
# 会话时间戳（如 "今天 17:33" / "17:33"）
_TS_RE = re.compile(r"^(今天|昨天|星期.)?\s*\d{1,2}:\d{2}(:\d{2})?$")


class DoubaoError(RuntimeError):
    pass


def _run(args: list, timeout: int = 90) -> str:
    """调 winapp ui，返回 stdout；非零退出抛 DoubaoError。"""
    try:
        p = subprocess.run(
            [WINAPP, "ui", *args],
            capture_output=True, text=True, encoding="utf-8-sig", errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise DoubaoError(f"winapp 超时: {' '.join(args)}")
    out = (p.stdout or "") + (p.stderr or "")
    if p.returncode != 0:
        raise DoubaoError(out.strip()[:300] or f"winapp 退出码 {p.returncode}")
    return out


_PUNCT_RE = re.compile(r"[\s，,。.；;：:？?！!、'\"'\"（）()【】《》<>~·…—_\-\[\]{}]+")


def _norm(s: str) -> str:
    """去空白+标点比较用。
    豆包编辑器会把半角 [] 自动转成全角 【】，回显与原文括号形态不一致，必须两类都剥。"""
    return _PUNCT_RE.sub("", s)


def ensure_window() -> None:
    """确保豆包主窗口可见（主窗口常藏托盘，Start-Process 可唤出并置前台）。"""
    if "Found 0 windows" not in _run(["list-windows", "-a", APP]):
        return
    if not DOUBAO_EXE.exists():
        raise DoubaoError(f"未找到豆包: {DOUBAO_EXE}")
    subprocess.Popen([str(DOUBAO_EXE)], close_fds=True)
    deadline = time.time() + 30
    while time.time() < deadline:
        time.sleep(2)
        try:
            if "Found 0 windows" not in _run(["list-windows", "-a", APP]):
                time.sleep(2)  # 等渲染稳定
                return
        except DoubaoError:
            pass
    raise DoubaoError("唤出豆包窗口超时")


def _hwnd() -> int:
    out = _run(["list-windows", "-a", APP])
    m = re.search(r"HWND\s+(\d+)", out)
    if not m:
        raise DoubaoError("找不到豆包窗口句柄")
    return int(m.group(1))


def activate() -> None:
    """把豆包窗口置前台——send-input 的前置条件。
    Start-Process 在 Windows 前台锁下常被拒；改用 ctypes：先补一个 Alt 键事件
    （前台锁放行"最后输入源"进程），再 ShowWindow + SetForegroundWindow。"""
    import ctypes
    for _ in range(3):
        try:
            if ", foreground" in _run(["list-windows", "-a", APP]):
                return
            hwnd = _hwnd()
            u = ctypes.windll.user32
            u.keybd_event(0x12, 0, 0, 0)       # Alt down：成为最后输入源
            u.keybd_event(0x12, 0, 2, 0)       # Alt up
            # SW_RESTORE 会把最大化窗口打回原形，仅最小化时才用
            u.ShowWindow(hwnd, 9 if u.IsIconic(hwnd) else 5)
            u.SetForegroundWindow(hwnd)
        except DoubaoError:
            pass
        time.sleep(2)
    try:
        if ", foreground" in _run(["list-windows", "-a", APP]):
            return
    except DoubaoError:
        pass
    raise DoubaoError("无法把豆包窗口置前台")


def _input_ready() -> bool:
    """输入框空闲（占位符可见）。实际占位符带省略号后缀，必须子串匹配。"""
    try:
        return any(INPUT_PLACEHOLDER in t for t in dump_texts())
    except DoubaoError:
        return False


def new_chat() -> None:
    """Ctrl+Shift+K 开新会话，避免污染旧对话。
    必须等输入框占位符出现后再额外等 2.5s：欢迎页推荐 chips 渲染较慢，
    过早采 baseline 会把后渲染的 chips 误判成回复。
    若占位符迟迟不出现，可能是有旧草稿挡住——全选删除清输入框再试。"""
    _run(["send-keys", "ctrl+shift+k", "--via", "send-input", "-a", APP])
    if _wait_input(10):
        time.sleep(2.5)
        return
    _run(["send-keys", "ctrl+a", "--via", "send-input", "-a", APP])
    _run(["send-keys", "backspace", "--via", "send-input", "-a", APP])
    if _wait_input(10):
        time.sleep(2.5)
        return
    raise DoubaoError("新会话页面未就绪（未等到输入框）")


def _wait_input(timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1.5)
        if _input_ready():
            return True
    return False


def dump_state() -> tuple:
    """dump 全树，返回 (聊天区文本列表, 猜你想问 chips 是否出现)。
    必须用 --json：文本格式会把兄弟节点 '+more' 截断，且 JSON 完整不丢深层节点。
    文本过滤：suggest/video 卡子树、左栏会话列表、顶部栏会话标题。
    chips 是回答完成的可靠信号——文本先稳定后豆包还会做后加工（删复述/插卡）。"""
    out = _run(["inspect", "-a", APP, "--depth", INSPECT_DEPTH, "--json"], timeout=120)
    try:
        data = json.loads(out.lstrip("\ufeff"))
    except ValueError as e:
        raise DoubaoError(f"inspect JSON 解析失败: {str(e)[:80]}")
    texts = []
    chips = False

    def walk(node: dict, win_x: int, win_y: int, chat_x: int) -> None:
        nonlocal chips
        cls = node.get("className") or ""
        if any(c in cls for c in _CARD_CLASSES):
            if "suggest-list-item" in cls:
                chips = True
            return
        if node.get("type") == "Text":
            name = node.get("name")
            rel_x = node.get("x", 0) - win_x
            rel_y = node.get("y", 0) - win_y
            # 剔左栏会话列表（预览会随当前会话更新而泄漏）；剔可见顶部栏会话标题；
            # 滚出屏顶/屏左的 rel<0 必须保留（可能是回复正文）
            if name and rel_x >= chat_x and not (0 <= rel_y < HEADER_ZONE):
                texts.append(name)
        for ch in node.get("children") or []:
            walk(ch, win_x, win_y, chat_x)

    for win in data.get("windows", []):
        els = win.get("elements", [])
        win_x = els[0].get("x", 0) if els else 0
        win_y = els[0].get("y", 0) if els else 0
        chat_x = int(350 * win.get("scale", 2))      # 侧栏 CSS 宽 350，物理像素乘 scale
        for el in els:
            walk(el, win_x, win_y, chat_x)
    return texts, chips


def dump_texts() -> list:
    return dump_state()[0]


def type_prompt(text: str) -> None:
    """真键盘输入（唯一需要前台的动作）。>32 字符 winapp 会自动分块，较慢。
    换行必须换成全角分号：\\n 可能被 --verbatim 译成 Enter 把半截消息发出去。
    打完验证占位符消失，否则重激活重打。"""
    text = re.sub(r"\s*\n\s*", "；", text.strip())
    for _ in range(3):
        _run(["send-keys", "--verbatim", text,
              "--target", INPUT_PLACEHOLDER, "--via", "send-input", "-a", APP],
             timeout=max(120, 15 + len(text) // 3))
        if not _input_ready():      # 占位符消失 = 字进输入框
            return
        time.sleep(1.5)
        activate()
    raise DoubaoError("打字失败（输入框始终为空）")


def click_send() -> None:
    """点发送并确认消息真的离开输入框。
    按钮在打完字后异步置亮，立刻 invoke 会撞 display-only，需重试等待；
    invoke 偶尔被吞，最后补一次回车。"""
    for _ in range(6):
        try:
            _run(["invoke", SEND_BUTTON, "-a", APP])
            break
        except DoubaoError as e:
            if "invoke pattern" not in str(e):
                raise
            time.sleep(1.5)
    else:
        raise DoubaoError("发送按钮一直不可触发")
    if _wait_sent(8):
        return
    _run(["send-keys", "enter", "--via", "send-input", "-a", APP])
    if _wait_sent(8):
        return
    raise DoubaoError("消息发送失败（输入框未清空）")


def _wait_sent(timeout: float) -> bool:
    """发送成功的判据：输入框清空、占位符重新出现。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1)
        if _input_ready():
            return True
    return False


def wait_reply(prompt: str, timeout: int, baseline_set: set) -> str:
    """轮询全树 diff，拿到最终回复。
    完成判据（三道闸，缺一不可的教训堆出来的）：
      1. 不能用 send 按钮当空闲信号——输入框清空后它会从树里消失（换成语音按钮）；
      2. 必须先确认进入会话页（用户回显出现/欢迎语消失），否则欢迎页内容卡轮播会污染；
      3. 必须等猜你想问 chips 出现——文本先稳定后豆包还会后加工（删复述/插相关视频卡），
         只看文本稳定会在 9s 安静窗里误判完成；chips 迟迟不来则 8 轮稳定兜底。"""
    deadline = time.time() + timeout
    pn = _norm(prompt)[:60]
    if DEBUG:
        with open(DEBUG, "a", encoding="utf-8") as f:
            f.write(f"=== prompt={prompt!r} pn={pn!r}\n")
    last, stable = "", 0
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        try:
            texts, chips = dump_state()
        except DoubaoError:
            continue
        if not _conversation_started(texts, baseline_set, pn):
            last, stable = "", 0
            continue
        reply = _extract_reply(texts, baseline_set, prompt)
        if DEBUG:
            with open(DEBUG, "a", encoding="utf-8") as f:
                f.write(f"--- chips={chips} stable={stable} reply={reply!r}\n")
                for t in texts:
                    f.write(f"  {t!r}\n")
        if reply and reply == last:
            stable += 1
            if chips and stable >= SETTLE_ROUNDS:
                return reply
            if stable >= 8:                      # chips 不出现的回答类型兜底（约 24s 稳定）
                return reply
        else:
            last, stable = reply, 0
    raise DoubaoError(f"等待豆包回复超过 {timeout}s")


def _conversation_started(texts: list, baseline_set: set, pn: str) -> bool:
    """用户消息回显（或其片段）出现在树里，或欢迎语消失，即认为已进入会话页。"""
    for t in texts:
        if t in baseline_set:
            continue
        tn = _norm(t)
        if pn and len(tn) >= 8 and tn in pn:
            return True
    return not any("有什么我能帮你的吗" in t for t in texts)


def _extract_reply(texts: list, baseline_set: set, prompt: str) -> str:
    """从全树文本中挑出本轮新增且属于豆包回复的部分。"""
    pn = _norm(prompt)[:60]
    parts = []
    for t in texts:
        t = t.strip()
        if not t or len(t) < 2:
            continue
        if t in baseline_set:          # 发送前就存在的旧文本
            continue
        if any(t.startswith(p) for p in NOISE_PREFIXES):
            continue
        if _TS_RE.match(t):            # 会话时间戳
            continue
        tn = _norm(t)
        if pn and (tn in pn or pn.startswith(tn[:20])):  # 用户消息回显/会话标题
            continue
        parts.append(t)
    return "\n".join(parts)


def ask(prompt: str, timeout: int = 300, fresh: bool = True) -> str:
    """完整一轮：唤窗 → (可选)新会话 → 打字 → 发送 → 等回复。返回回复文本。"""
    if not prompt.strip():
        raise DoubaoError("prompt 为空")
    ensure_window()
    activate()
    if fresh:
        new_chat()
    baseline = set(dump_texts())
    type_prompt(prompt)
    click_send()
    return wait_reply(prompt, timeout, baseline)


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]).strip() or "用一句话回答：1+1等于几？"
    t0 = time.time()
    try:
        ans = ask(q)
        print(json.dumps({"ok": True, "reply": ans,
                          "elapsed_s": round(time.time() - t0, 1)},
                         ensure_ascii=False, indent=2))
    except DoubaoError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)
