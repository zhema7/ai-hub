# -*- coding: utf-8 -*-
# ChatGPT 桌面端执行器（CDP 驱动，P1 平台扩展）
# 前提：ChatGPT.exe 带 --remote-debugging-port=9222 启动（start-chatgpt.ps1 负责）
# 链路：/json 找 app:// 页 → ws(suppress_origin=True，否则 403) → Runtime.evaluate 注入
#   打字：可见 .ProseMirror 聚焦 → selectAll+delete 清空 → execCommand insertText（走 PM 正常管线，可后台）
#   发送：button[aria-label~="发送|send"] click（限流时按钮 disabled，先查限流文案）
#   等回复：消息按 H4.sr-only 标记切分（"你说："/"ChatGPT 说："），新 AI turn 文本稳定 ×2 且无 stop 按钮
# 血泪点：
#   - 消息 DOM 无 data-message-author-role（那是网页版），桌面壳用 H4 sr-only 标记 + 兄弟节点为消息体
#   - 合成 KeyboardEvent / CDP Input.dispatchKeyEvent 的 Enter 均不被 PM 接受（isTrusted 问题），
#     按钮点不出来时基本是限流（body 含"已用完/速率限制"），不是事件管线问题
#   - execCommand insertText 可后台打字且 React 能感知（限流解除后按钮会亮）；Input.insertText 也行
#   - ws 必须 suppress_origin=True 否则 403；每次 ask 重连（页面 reload 容错）
import json
import re
import threading
import time

import requests
import websocket

CDP = "http://127.0.0.1:9222"
_glock = threading.Lock()   # 单实例 GUI，进程内也串行


class ChatGPTError(Exception):
    pass


def _connect(ws_timeout: float = 20):
    try:
        tabs = requests.get(f"{CDP}/json", timeout=5).json()
    except Exception as e:
        raise ChatGPTError(
            f"CDP {CDP} 不可达：ChatGPT 须带 --remote-debugging-port=9222 启动（跑 start-chatgpt.ps1）。{e}")
    pages = [t for t in tabs if t.get("type") == "page"
             and t.get("url", "").endswith("index.html")]
    if not pages:
        urls = [t.get("url") for t in tabs if t.get("type") == "page"][:5]
        raise ChatGPTError(f"未找到 app:// index.html 页面 target，现有 targets: {urls}")
    try:
        return websocket.create_connection(pages[0]["webSocketDebuggerUrl"],
                                           timeout=ws_timeout, suppress_origin=True)
    except Exception as e:
        raise ChatGPTError(f"ws 连接失败（必须 suppress_origin=True）: {e}")


def _ev(ws, expr: str, ev_timeout: float = 20):
    """Runtime.evaluate，返回 JS 值；JS 异常抛 ChatGPTError。"""
    mid = _ev._id = getattr(_ev, "_id", 0) + 1
    ws.send(json.dumps({"id": mid, "method": "Runtime.evaluate",
                        "params": {"expression": expr, "returnByValue": True, "awaitPromise": True}}))
    deadline = time.time() + ev_timeout
    while time.time() < deadline:
        m = json.loads(ws.recv())
        if m.get("id") == mid:
            if m.get("result", {}).get("exceptionDetails"):
                raise ChatGPTError("JS-ERR: " + json.dumps(m["result"]["exceptionDetails"])[:300])
            return m.get("result", {}).get("result", {}).get("value")
    raise ChatGPTError("Runtime.evaluate 超时")


# ---------- 页内 JS 片段 ----------
JS_BACK_TO_CHAT = r"""
(() => {
  const b = [...document.querySelectorAll('button, a')].find(e =>
    /返回 ?ChatGPT|Back to ChatGPT/i.test(e.innerText || ''));
  if (b) { b.click(); return 'switched'; }
  return 'not-found';
})()
"""

JS_NEW_CHAT = r"""
(() => {
  const b = [...document.querySelectorAll('button, a')].find(e =>
    (/新聊天|新对话|New ?chat/i.test(e.getAttribute('aria-label') || '') ||
     /新聊天|新对话|New ?chat/i.test(e.innerText || '')) &&
    e.getBoundingClientRect().width > 0);
  if (b) { b.click(); return 'new-chat'; }
  return 'no-btn';
})()
"""

JS_TYPE = r"""
(async (text) => {
  const eds = [...document.querySelectorAll('.ProseMirror')]
    .filter(e => e.getBoundingClientRect().width > 0);
  if (!eds.length) return {ok: false, why: 'no visible editor'};
  const ed = eds[0];
  ed.focus();
  document.execCommand('selectAll', false, null);
  document.execCommand('delete', false, null);
  const ok = document.execCommand('insertText', false, text);
  return {ok, len: ed.innerText.trim().length};
})(__PROMPT__)
"""

JS_STATE = r"""
(() => {
  // 消息结构：H4.sr-only 标记（你说：/ChatGPT 说：）+ 兄弟节点为消息体
  const marks = [...document.querySelectorAll('h4')].filter(h =>
    /^(你说|You said|ChatGPT 说|ChatGPT said)/.test((h.innerText || '').trim()));
  const turns = marks.map(h => {
    const body = [...h.parentElement.children].filter(ch => ch.tagName !== 'H4')
      .map(ch => ch.innerText).join('\n').trim();
    return {role: /^ChatGPT/i.test(h.innerText.trim()) ? 'ai' : 'user', text: body};
  });
  const stop = !!document.querySelector('[data-testid="stop-button"]') ||
    [...document.querySelectorAll('button')].some(b =>
      /停止|stop/i.test(b.getAttribute('aria-label') || '') &&
      b.getBoundingClientRect().width > 0);
  const eds = [...document.querySelectorAll('.ProseMirror')]
    .filter(e => e.getBoundingClientRect().width > 0);
  const send = [...document.querySelectorAll('button')].find(b =>
    /发送|send/i.test(b.getAttribute('aria-label') || '') &&
    b.getBoundingClientRect().width > 0);
  return {turns, stop, empty: eds.length ? !eds[0].innerText.trim() : null,
          sendReady: !!send && !send.disabled};
})()
"""

JS_LIMIT = r"(() => /已用完|速率限制|rate limit|usage limit/i.test(document.body.innerText))()"


def _norm(s: str) -> str:
    return "".join(s.split())


_NOISE_LINE = re.compile(r"^(\d{1,2}:\d{2}|最新一条回复|用时.*)$")


def _clean(text: str) -> str:
    """去掉消息体容器里的时间戳/徽标行。"""
    lines = [ln for ln in text.splitlines() if ln.strip() and not _NOISE_LINE.match(ln.strip())]
    return "\n".join(lines).strip()


def ask(prompt: str, timeout: int = 300) -> str:
    """向 ChatGPT 桌面端发一条消息并取回回复文本（每次新会话，无上下文残留）。"""
    if not prompt.strip():
        raise ChatGPTError("prompt 为空")
    with _glock:
        return _ask_locked(prompt, timeout)


def _ask_locked(prompt: str, timeout: int) -> str:
    ws = _connect(ws_timeout=max(20, timeout))
    try:
        # 0) Codex/ChatGPT 双模式兜底：无可见编辑器先点"返回 ChatGPT"
        probe = _ev(ws, r"""(() => {
          const eds = [...document.querySelectorAll('.ProseMirror')]
            .filter(e => e.getBoundingClientRect().width > 0);
          return eds.length ? 'has-editor' : 'no-editor';
        })()""")
        if probe == "no-editor":
            if _ev(ws, JS_BACK_TO_CHAT) == "switched":
                time.sleep(2)

        # 1) 开新会话（按钮不在/改名不阻塞主流程）
        try:
            if _ev(ws, JS_NEW_CHAT) == "new-chat":
                time.sleep(1.5)
        except ChatGPTError:
            pass

        # 2) 打字（重试 3 次，验证编辑器确实有内容）
        typed, r = False, None
        for _ in range(3):
            r = _ev(ws, JS_TYPE.replace("__PROMPT__", json.dumps(prompt)))
            if isinstance(r, dict) and r.get("ok") and r.get("len", 0) >= len(_norm(prompt)[:6]):
                typed = True
                break
            time.sleep(1)
        if not typed:
            raise ChatGPTError(f"打字失败（编辑器未收到内容）：{r}")

        # 3) 发送前基线
        st = _ev(ws, JS_STATE)
        if not isinstance(st, dict):
            raise ChatGPTError(f"页面状态异常: {st}")
        n_before = len(st["turns"])

        # 4) 点发送；15s 未亮先查限流，再报错
        sent = False
        for _ in range(15):
            st = _ev(ws, JS_STATE)
            if st.get("sendReady"):
                _ev(ws, r"""(() => {
                  const b = [...document.querySelectorAll('button')].find(b =>
                    /发送|send/i.test(b.getAttribute('aria-label') || '') &&
                    b.getBoundingClientRect().width > 0);
                  b.click();
                  return 'sent';
                })()""")
                sent = True
                break
            if _ev(ws, JS_LIMIT) is True:
                raise ChatGPTError("ChatGPT 额度受限（rate limit），等重置后再试")
            time.sleep(1)
        if not sent:
            raise ChatGPTError("发送按钮 15s 未亮（非限流；可能消息为空或页面未就绪）")

        # 5) 等回复：新 AI turn + 文本稳定 ×2 + 无 stop
        deadline = time.time() + timeout
        last_txt, stable = "", 0
        time.sleep(2)
        while time.time() < deadline:
            st = _ev(ws, JS_STATE)
            if not isinstance(st, dict):
                time.sleep(2)
                continue
            turns = st["turns"]
            last_ai = next((t for t in reversed(turns) if t["role"] == "ai"), None)
            if (len(turns) > n_before and last_ai and last_ai["text"]
                    and not st["stop"]):
                if last_ai["text"] == last_txt:
                    stable += 1
                    if stable >= 2:
                        return _clean(last_ai["text"])
                else:
                    stable = 0
                last_txt = last_ai["text"]
            time.sleep(3)
        raise ChatGPTError(f"等待回复超时（{timeout}s），最后状态 turns={len(st.get('turns', []))} stop={st.get('stop')}")
    finally:
        try:
            ws.close()
        except Exception:
            pass


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "用一句话介绍量子纠缠"
    print(ask(q))
