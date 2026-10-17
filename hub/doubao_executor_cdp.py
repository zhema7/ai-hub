# -*- coding: utf-8 -*-
# 豆包桌面端执行器 · CDP 后端（P2 后台化，2026-10-17）
# 对比 winapp UIA 版（doubao_executor.py）：全程 Runtime.evaluate 注入零抢焦点，无需前台激活。
# 前提：Doubao.exe 带 --remote-debugging-port=9223 启动（start-doubao.ps1 已支持）。
# 结构结论（实测）：
#   - 聊天页 target = doubao://doubao-chat/chat（勿连 doubao-background）
#   - 输入框 .tiptap ProseMirror；发送按钮 #flow-end-msg-send（composer 有内容才出现）
#   - 消息正文 [data-testid="message_text_content"]，data-streaming 属性直读流式状态，顺序 user→AI
#   - 完成信号：最后一条 streaming=false + 文本稳定 + .suggest-list-item chips（豆包后加工完才出）
#   - 豆包 tiptap 把半角 [] 转全角 【】，回显比对必须半全角归一
import json
import re
import threading
import time

import requests
import websocket

CDP = "http://127.0.0.1:9223"
_glock = threading.Lock()


class DoubaoError(Exception):
    pass


def _connect(ws_timeout: float = 20):
    try:
        tabs = requests.get(f"{CDP}/json", timeout=5).json()
    except Exception as e:
        raise DoubaoError(
            f"CDP {CDP} 不可达：豆包须带 --remote-debugging-port=9223 启动（跑 start-doubao.ps1）。{e}")
    pages = [t for t in tabs if t.get("type") == "page" and "doubao-chat" in t.get("url", "")]
    if not pages:
        urls = [t.get("url") for t in tabs if t.get("type") == "page"][:5]
        raise DoubaoError(f"未找到 doubao-chat 页面 target，现有: {urls}")
    try:
        return websocket.create_connection(pages[0]["webSocketDebuggerUrl"],
                                           timeout=ws_timeout, suppress_origin=True)
    except Exception as e:
        raise DoubaoError(f"ws 连接失败: {e}")


def _ev(ws, expr: str, ev_timeout: float = 20):
    mid = _ev._id = getattr(_ev, "_id", 0) + 1
    ws.send(json.dumps({"id": mid, "method": "Runtime.evaluate",
                        "params": {"expression": expr, "returnByValue": True, "awaitPromise": True}}))
    deadline = time.time() + ev_timeout
    while time.time() < deadline:
        m = json.loads(ws.recv())
        if m.get("id") == mid:
            if m.get("result", {}).get("exceptionDetails"):
                raise DoubaoError("JS-ERR: " + json.dumps(m["result"]["exceptionDetails"])[:300])
            return m.get("result", {}).get("result", {}).get("value")
    raise DoubaoError("Runtime.evaluate 超时")


JS_NEW_CHAT = r"""
(() => {
  const b = [...document.querySelectorAll('button, [role="button"], a')].find(e =>
    (/新建对话|新聊天|新会话|New ?chat/i.test(e.getAttribute('aria-label') || '') ||
     /^(新建对话|新聊天|新会话)$/.test((e.innerText || '').trim())) &&
    e.getBoundingClientRect().width > 0);
  if (b) { b.click(); return 'new-chat'; }
  return 'no-btn';
})()
"""

JS_TYPE = r"""
(async (text) => {
  const ed = [...document.querySelectorAll('.ProseMirror')]
    .filter(e => e.getBoundingClientRect().width > 0)[0];
  if (!ed) return {ok: false, why: 'no visible editor'};
  ed.focus();
  document.execCommand('selectAll', false, null);
  document.execCommand('delete', false, null);
  const ok = document.execCommand('insertText', false, text);
  return {ok, len: ed.innerText.trim().length};
})(__PROMPT__)
"""

JS_STATE = r"""
(() => {
  const tcs = [...document.querySelectorAll('[data-testid="message_text_content"]')]
    .filter(t => t.getBoundingClientRect().width > 0)
    .map(t => ({streaming: t.getAttribute('data-streaming') !== 'false',
                text: t.innerText.trim()}));
  const ed = [...document.querySelectorAll('.ProseMirror')]
    .filter(e => e.getBoundingClientRect().width > 0)[0];
  return {tcs,
          composer: ed ? ed.innerText.trim() : null,
          chips: !!document.querySelector('[class*="suggest-list-item"]'),
          sendReady: (() => {
            const s = document.querySelector('#flow-end-msg-send');
            return !!s && !s.disabled && !/disabled/.test(s.className || '') &&
                   s.getBoundingClientRect().width > 0;
          })()};
})()
"""

_PUNCT = re.compile(r"[，。！？；：、（）【】\[\]「」《》…·,.!?;:()<>\"'`~@#$%^&*_+=|\\/\s\u3000]")


def _norm(s: str) -> str:
    """半全角标点全剥（豆包 tiptap 会把 [] 转全角【】）。"""
    return _PUNCT.sub("", s or "")


def ask(prompt: str, timeout: int = 180) -> str:
    """向豆包桌面端发一条消息并取回回复（CDP 注入，全程后台）。"""
    if not prompt.strip():
        raise DoubaoError("prompt 为空")
    with _glock:
        return _ask_locked(prompt, timeout)


def _ask_locked(prompt: str, timeout: int) -> str:
    ws = _connect(ws_timeout=max(20, timeout))
    try:
        # 1) 新会话（找不到按钮不阻塞——发送后在旧会话续问也能正确提取最后一条）
        try:
            if _ev(ws, JS_NEW_CHAT) == "new-chat":
                time.sleep(1.5)
        except DoubaoError:
            pass

        # 2) 打字（重试 3 次）
        typed, r = False, None
        for _ in range(3):
            r = _ev(ws, JS_TYPE.replace("__PROMPT__", json.dumps(prompt)))
            if isinstance(r, dict) and r.get("ok") and r.get("len", 0) >= len(_norm(prompt)[:6]):
                typed = True
                break
            time.sleep(1)
        if not typed:
            raise DoubaoError(f"打字失败（编辑器未收到内容）：{r}")

        # 3) 基线
        st = _ev(ws, JS_STATE)
        n_before = len(st["tcs"]) if isinstance(st, dict) else 0
        pnorm = _norm(prompt)

        # 4) 等发送按钮亮 → click（composer 有内容 #flow-end-msg-send 才出现）
        sent = False
        for _ in range(15):
            st = _ev(ws, JS_STATE)
            if st.get("sendReady"):
                _ev(ws, r"""(() => {
                  const b = document.querySelector('#flow-end-msg-send');
                  b.click();
                  return 'sent';
                })()""")
                sent = True
                break
            time.sleep(1)
        if not sent:
            raise DoubaoError("发送按钮 15s 未出现/不可点（composer 可能为空或页面未就绪）")

        # 5) 等回复：新消息 + streaming=false + 文本稳定 + chips（或加长稳定兜底）
        deadline = time.time() + timeout
        last_txt, stable = "", 0
        time.sleep(2)
        while time.time() < deadline:
            st = _ev(ws, JS_STATE)
            if not isinstance(st, dict):
                time.sleep(2)
                continue
            tcs = st["tcs"]
            if len(tcs) > n_before:
                last = tcs[-1]
                if (not last["streaming"] and last["text"] and
                        _norm(last["text"]) != pnorm):
                    if last["text"] == last_txt:
                        stable += 1
                        if stable >= 2 and st["chips"]:
                            return last["text"]
                        if stable >= 4:      # chips 不来（部分回答类型没有）兜底
                            return last["text"]
                    else:
                        stable = 0
                    last_txt = last["text"]
            time.sleep(3)
        raise DoubaoError(f"等待回复超时（{timeout}s），最后状态 msgs={len(st.get('tcs', []))} chips={st.get('chips')}")
    finally:
        try:
            ws.close()
        except Exception:
            pass


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "用一句话介绍光合作用"
    print(ask(q))
