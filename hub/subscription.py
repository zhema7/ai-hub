# -*- coding: utf-8 -*-
# AI Hub · 订阅状态探测（读用户真实订阅，替代硬编码年费）
#
# 思路：
#   各 App 通常不直接返回「价格」，但会返回「计划名」（planType / 会员等级）。
#   我们用 CDP / 本地 API 读到计划名，再查 PLAN_FEE 表得到价格。
#   探测结果缓存到 hub/data/subscriptions.json，看板优先用缓存，探测失败回退 cost.py 硬编码。
#
# 用法：
#   python subscription.py                  探测所有在线通道并刷新缓存
#   python subscription.py chatgpt-app      只探某个通道
import json
import time
from pathlib import Path

import cost

ROOT = Path(__file__).resolve().parent
CACHE_FILE = ROOT / "data" / "subscriptions.json"

# 计划名 → 年费（CNY, USD）。价格取公开订阅价，标记 est.
# 读不到计划时回退 cost.py RATES 里的 sub_cny/sub_usd。
PLAN_FEE: dict[str, dict[str, dict]] = {
    "chatgpt-app": {
        "free":      {"cny": 0,    "usd": 0,    "label": "Free"},
        "plus":      {"cny": 1600, "usd": 240,  "label": "Plus"},
        "pro":       {"cny": 2400, "usd": 360,  "label": "Pro"},
        "team":      {"cny": 3200, "usd": 480,  "label": "Team"},
        "enterprise":{"cny": 0,    "usd": 0,    "label": "Enterprise"},
    },
    "kimi-vip": {
        "free":      {"cny": 0,    "usd": 0,    "label": "免费版"},
        "pro":       {"cny": 499,  "usd": 70,   "label": "Kimi Code Pro"},
        "ultra":     {"cny": 999,  "usd": 140,  "label": "Kimi Code Ultra"},
    },
    "doubao-app": {
        "free":      {"cny": 0,    "usd": 0,    "label": "免费版"},
        "member":    {"cny": 198,  "usd": 28,   "label": "豆包会员"},
        "super":     {"cny": 398,  "usd": 56,   "label": "豆包超级会员"},
    },
    "workbuddy-app": {
        "free":      {"cny": 0,    "usd": 0,    "label": "免费版"},
        "pro":       {"cny": 360,  "usd": 50,   "label": "WorkBuddy Pro"},
    },
}

# 各通道 CDP 端口 / 探测入口
PORTS = {"chatgpt-app": 9222, "doubao-app": 9223, "workbuddy-app": 9224}


def _load_cache() -> dict:
    if CACHE_FILE.is_file():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_cache(d: dict):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def get(channel: str) -> dict | None:
    """返回 {channel, plan, label, fee_cny, fee_usd, detected_at, source}；无缓存返回 None。"""
    cache = _load_cache()
    rec = cache.get(channel)
    if not rec:
        return None
    return rec


# ---------------- 各通道探测 ----------------

def _ws_cdp(port: int, url_filter: str = ""):
    """返回 (ws, eval_fn)，连到含 url_filter 的 page/webview target。"""
    import requests, websocket
    allt = requests.get(f"http://127.0.0.1:{port}/json", timeout=5).json()
    targets = [t for t in allt
               if t.get("type") in ("page", "webview", "iframe")
               and (not url_filter or url_filter in t.get("url", ""))]
    if not targets:
        targets = [t for t in allt if t.get("type") in ("page", "webview")]
    if not targets:
        raise RuntimeError(f"CDP :{port} 无可用 target")
    ws = websocket.create_connection(targets[0]["webSocketDebuggerUrl"],
                                     timeout=15, suppress_origin=True)
    mid = [0]

    def ev(expr, to=20):
        mid[0] += 1
        ws.send(json.dumps({"id": mid[0], "method": "Runtime.evaluate",
                            "params": {"expression": expr, "returnByValue": True, "awaitPromise": True}}))
        dl = time.time() + to
        while time.time() < dl:
            m = json.loads(ws.recv())
            if m.get("id") == mid[0]:
                exc = m.get("result", {}).get("exceptionDetails")
                if exc:
                    raise RuntimeError("JS-ERR: " + json.dumps(exc)[:200])
                return m.get("result", {}).get("result", {}).get("value")
        raise RuntimeError("eval timeout")

    return ws, ev


def detect_chatgpt() -> dict:
    """读 chatgpt.com /api/auth/session → account.planType。"""
    ws, ev = _ws_cdp(9222, "chatgpt.com")
    try:
        res = ev(r"""(async()=>{const r=await fetch('/api/auth/session',{credentials:'include'});
                     const j=await r.json(); return (j.account&&j.account.planType)||'free';})()""")
        plan = (res or "free").lower()
        fee = PLAN_FEE["chatgpt-app"].get(plan, PLAN_FEE["chatgpt-app"]["plus"])
        return {"plan": plan, **fee}
    finally:
        try: ws.close()
        except Exception: pass


def detect_doubao() -> dict:
    """豆包：从账号页 DOM 文本匹配会员等级。"""
    ws, ev = _ws_cdp(9223, "doubao-chat")
    try:
        txt = ev("document.body.innerText") or ""
        low = txt.lower()
        if "超级会员" in txt: plan = "super"
        elif any(k in txt for k in ("会员", "member", "vip")): plan = "member"
        else: plan = "free"
        fee = PLAN_FEE["doubao-app"].get(plan, PLAN_FEE["doubao-app"]["free"])
        return {"plan": plan, **fee}
    finally:
        try: ws.close()
        except Exception: pass


def detect_workbuddy() -> dict:
    """WorkBuddy：从账号页/设置页 DOM 文本匹配 pro。"""
    ws, ev = _ws_cdp(9224, "")
    try:
        txt = ev("document.body.innerText") or ""
        plan = "pro" if any(k in txt for k in ("Pro", "专业版", "已订阅")) else "free"
        fee = PLAN_FEE["workbuddy-app"].get(plan, PLAN_FEE["workbuddy-app"]["free"])
        return {"plan": plan, **fee}
    finally:
        try: ws.close()
        except Exception: pass


def detect_kimi() -> dict:
    """Kimi Code：读本地 API /api/v1/user 的订阅字段。"""
    import httpx
    token = (ROOT.parent / "ai-relay" / ".kimi-web-token").read_text(encoding="utf-8-sig").strip()
    base = "http://127.0.0.1:58628"
    headers = {"Authorization": f"Bearer {token}"}
    for ep in ("/api/v1/user", "/api/v1/account", "/api/v1/users/me"):
        try:
            with httpx.Client(trust_env=False, timeout=5) as c:
                r = c.get(base + ep, headers=headers)
                if r.status_code == 200:
                    j = r.json().get("data", r.json())
                    plan = str(j.get("plan") or j.get("tier") or j.get("subscription") or "free").lower()
                    for k in ("pro", "ultra", "premium"):
                        if k in plan: plan = k; break
                    fee = PLAN_FEE["kimi-vip"].get(plan, PLAN_FEE["kimi-vip"]["pro"])
                    return {"plan": plan, **fee}
        except Exception:
            continue
    raise RuntimeError("kimi 本地服务(58628)未启动或无账号端点")


DETECTORS = {"chatgpt-app": detect_chatgpt, "doubao-app": detect_doubao,
             "workbuddy-app": detect_workbuddy, "kimi-vip": detect_kimi}


def refresh(channel: str = "") -> dict:
    """探测指定（或全部在线）通道，刷新缓存，返回 {channel: result}。"""
    cache = _load_cache()
    targets = [channel] if channel else list(DETECTORS)
    results = {}
    for ch in targets:
        fn = DETECTORS.get(ch)
        if not fn:
            continue
        try:
            r = fn()
            rec = {"channel": ch, **r, "detected_at": time.time(),
                   "source": f"{ch} probe"}
            cache[ch] = rec
            results[ch] = rec
            print(f"[ok] {ch}: plan={r['plan']} label={r['label']} fee=¥{r['cny']}/${r['usd']}/年")
        except Exception as e:
            print(f"[skip] {ch}: {type(e).__name__}: {str(e)[:80]}")
    _save_cache(cache)
    return results


if __name__ == "__main__":
    import sys
    refresh(sys.argv[1] if len(sys.argv) > 1 else "")
