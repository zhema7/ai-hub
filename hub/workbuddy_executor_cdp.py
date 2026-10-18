# -*- coding: utf-8 -*-
# WorkBuddy 桌面端执行器 · CDP 后端（2026-10-18，模式复刻 doubao_executor_cdp）
# 前提：WorkBuddy.exe 带 --remote-debugging-port=9224 启动（start-workbuddy.ps1）。
# 结构结论（实测）：
#   - 页面 target = 唯一 page（本地 app.asar 渲染器）；Electron 单例，须先杀旧实例再带参拉起
#   - 输入框 = Slate.js [contenteditable="true"]；★严禁 execCommand selectAll/delete（会把
#     Slate 节点跟踪打崩，报 removeChild 错、发送键永久灰）——清空靠 Page.reload，
#     打字只走 CDP Input.insertText（浏览器级可信事件，Slate 正常入账）
#   - 发送键 = button[aria-label="发送"]（cr-send-button）；门槛=编辑器 Slate 模型非空
#     + 已选工作空间（cr-workspace-picker）；崩溃残留态会让它永久 disabled，Page.reload 可解
#   - 回复正文 = .cr-markdown（用户消息在 cr-self-bubble，互不污染）；生成中 stop 按钮在场，
#     完成后消失 + 消息头出现 "已完成 Xs" + 消耗积分（如 "共消耗 0.33 快速 (GLM…)"）
#   - WorkBuddy 是任务型 Agent：指令里应明确"直接回复文本，不要调用工具/创建文档"，
#     否则它可能在自有工作区产出文件（本执行器只提取会话文本）
import json
import os
import threading
import time

import requests
import websocket

CDP = "http://127.0.0.1:9224"
WORKSPACE = os.environ.get("WB_WORKSPACE", "")   # 指定工作空间名；空=沿用当前/列表首个
_glock = threading.Lock()


class WorkBuddyError(Exception):
    pass


def _connect(ws_timeout: float = 20):
    try:
        tabs = requests.get(f"{CDP}/json", timeout=5).json()
    except Exception as e:
        raise WorkBuddyError(
            f"CDP {CDP} 不可达：WorkBuddy 须带 --remote-debugging-port=9224 启动（跑 start-workbuddy.ps1）。{e}")
    pages = [t for t in tabs if t.get("type") == "page"]
    if not pages:
        raise WorkBuddyError("未找到 page target")
    try:
        return websocket.create_connection(pages[0]["webSocketDebuggerUrl"],
                                           timeout=ws_timeout, suppress_origin=True)
    except Exception as e:
        raise WorkBuddyError(f"ws 连接失败: {e}")


class _Session:
    def __init__(self, ws):
        self.ws = ws
        self._id = 0

    def cmd(self, method, params=None, timeout=25):
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            m = json.loads(self.ws.recv())
            if m.get("id") == self._id:
                return m
        raise WorkBuddyError(f"{method} 超时")

    def ev(self, expr, timeout=25):
        m = self.cmd("Runtime.evaluate",
                     {"expression": expr, "returnByValue": True, "awaitPromise": True}, timeout)
        if m.get("result", {}).get("exceptionDetails"):
            raise WorkBuddyError("JS-ERR: " + json.dumps(m["result"]["exceptionDetails"])[:300])
        return m.get("result", {}).get("result", {}).get("value")


JS_EDITOR_STATE = r"""
(() => {
  const ed = [...document.querySelectorAll('[contenteditable="true"]')]
    .filter(e => e.getBoundingClientRect().width > 0)[0];
  const crash = (document.body.innerText || '').includes('输入框遇到错误');
  const picker = [...document.querySelectorAll('[class*="cr-workspace-picker"]')]
    .filter(e => e.getBoundingClientRect().width > 0)[0];
  return {editor: !!ed, crash,
          workspace: picker ? (picker.innerText || '').trim() : ''};
})()
"""

JS_SEND_STATE = r"""
(() => {
  const s = [...document.querySelectorAll('button')].find(b => b.getAttribute('aria-label') === '发送');
  return s ? !s.disabled && s.getBoundingClientRect().width > 0 : false;
})()
"""

JS_REPLY_STATE = r"""
(() => {
  const vis = e => e.getBoundingClientRect().width > 0;
  const mds = [...document.querySelectorAll('.cr-markdown')].filter(vis)
    .map(e => (e.innerText || '').trim()).filter(Boolean);
  const stop = [...document.querySelectorAll('button')].some(b =>
    /停止|stop/i.test((b.getAttribute('aria-label') || '') + (b.innerText || '')) && vis(b));
  return {mds, stop};
})()
"""


def _wait_editor(s: _Session, tries: int = 20) -> dict:
    """等编辑器就绪；遇崩溃态点 重试。"""
    for _ in range(tries):
        st = s.ev(JS_EDITOR_STATE)
        if isinstance(st, dict):
            if st.get("crash"):
                s.ev(r"""(() => { const b = [...document.querySelectorAll('button, [role="button"], a')]
                  .find(e => (e.innerText || '').trim() === '重试' && e.getBoundingClientRect().width > 0);
                  if (b) b.click(); return 1; })()""")
                time.sleep(2)
                continue
            if st.get("editor"):
                return st
        time.sleep(1)
    raise WorkBuddyError("编辑器 20s 未就绪")


def _ensure_workspace(s: _Session, st: dict):
    ws_name = (st.get("workspace") or "")
    if ws_name and "选择工作空间" not in ws_name:
        return                                   # 已绑定工作空间
    s.ev(r"""(() => { const p = [...document.querySelectorAll('[class*="cr-workspace-picker"]')]
      .filter(e => e.getBoundingClientRect().width > 0)[0]; if (p) p.click(); return 1; })()""")
    time.sleep(1)
    want = WORKSPACE
    picked = s.ev(r"""
    ((want) => { const items = [...document.querySelectorAll('.wb-dropdown__item')]
      .filter(e => e.getBoundingClientRect().width > 0 && (e.innerText || '').trim()
                      && !/新建工作空间|打开本地文件夹/.test(e.innerText));
      const it = (want && items.find(e => (e.innerText || '').trim().startsWith(want))) || items[0];
      if (it) { it.click(); return (it.innerText || '').trim(); } return null; })(%s)
    """ % json.dumps(want))
    if not picked:
        raise WorkBuddyError("工作空间下拉无可选项（请先在 WorkBuddy 里建一个工作空间）")
    time.sleep(1.5)


def ask(prompt: str, timeout: int = 300) -> str:
    """向 WorkBuddy 发一条任务消息并取回文本回复（CDP 注入，全程后台）。"""
    if not prompt.strip():
        raise WorkBuddyError("prompt 为空")
    with _glock:
        return _ask_locked(prompt, timeout)


def _ask_locked(prompt: str, timeout: int) -> str:
    ws = _connect(ws_timeout=30)
    s = _Session(ws)
    try:
        # 1) reload 清崩溃态/输入残留（Slate 编辑器不可 execCommand 清空）
        s.cmd("Page.reload")
        time.sleep(6)
        st = _wait_editor(s)
        _ensure_workspace(s, st)

        # 2) 打字：仅 Input.insertText（浏览器级可信事件，Slate 正常入账）
        s.ev(r"""(() => { const ed = [...document.querySelectorAll('[contenteditable="true"]')]
          .filter(e => e.getBoundingClientRect().width > 0)[0]; ed.focus(); return 1; })()""")
        time.sleep(0.3)
        s.cmd("Input.insertText", {"text": prompt})
        time.sleep(1)
        st = s.ev(JS_REPLY_STATE)
        n_before = len(st["mds"]) if isinstance(st, dict) else 0

        # 3) 等发送键亮 → click
        sent = False
        for _ in range(15):
            if s.ev(JS_SEND_STATE):
                s.ev(r"""(() => { [...document.querySelectorAll('button')]
                  .find(b => b.getAttribute('aria-label') === '发送' && !b.disabled).click(); return 1; })()""")
                sent = True
                break
            time.sleep(1)
        if not sent:
            raise WorkBuddyError("发送键 15s 未亮（Slate 未入账或工作空间未选）")

        # 4) 等回复：新 .cr-markdown + stop 消失 + 文本稳定×2
        deadline = time.time() + timeout
        last_txt, stable = "", 0
        time.sleep(2)
        while time.time() < deadline:
            st = s.ev(JS_REPLY_STATE)
            if isinstance(st, dict):
                mds = st["mds"]
                if len(mds) > n_before:
                    last = mds[-1]
                    if not st["stop"] and last:
                        if last == last_txt:
                            stable += 1
                            if stable >= 2:
                                return last
                        else:
                            stable = 0
                        last_txt = last
            time.sleep(3)
        raise WorkBuddyError(f"等待回复超时（{timeout}s）")
    finally:
        try:
            ws.close()
        except Exception:
            pass


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "用一句话介绍光合作用，直接回复文本，不要调用任何工具"
    print(ask(q))
