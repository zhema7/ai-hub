# -*- coding: utf-8 -*-
# 豆包 App 执行器 → OpenAI 兼容接口 适配桥（127.0.0.1:4301）
# 对外：POST /v1/chat/completions（模型名 doubao-app），内部经执行器驱动豆包桌面端
# 花的是豆包 App 会员/免费额度，零 API Key；可挂进 LiteLLM 网关当档位
# 启动：.\start-doubao.ps1（复用 ai-relay\.venv）
# 后端切换（P2 起默认 CDP）：
#   CDP 后端  = doubao_executor_cdp（Runtime.evaluate 注入，全程后台零抢焦点，须 --remote-debugging-port=9223）
#   UIA 后端  = doubao_executor（winapp send-input 打字，须短暂前台；CDP 不可用时改回此行）
from doubao_executor_cdp import DoubaoError, ask
import asyncio
import json
import re
import time
import uuid

import cost
import metrics_log

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

MODEL = "doubao-app"
COST_CLASS = "free_app"
_LIMIT_HINT = ("限流", "已用完", "速率", "rate limit", "too many", "quota")
app = FastAPI()
_lock = asyncio.Lock()          # GUI 只有一个，所有请求串行


def _note(e, ok=True) -> str:
    """区分限流命中与普通失败（看板额度水位靠 note 前缀聚合）。"""
    s = str(e)
    low = s.lower()
    return ("limit: " if any(k in low for k in _LIMIT_HINT) else "error: ") + s[:100]


def _flatten(messages: list) -> str:
    """把 OpenAI messages 压成单条 prompt（保留多轮历史）。"""
    parts = []
    for m in messages:
        role, content = m.get("role", "user"), m.get("content", "")
        if isinstance(content, list):
            content = "\n".join(c.get("text", "") for c in content
                                if isinstance(c, dict) and c.get("type") == "text")
        if not content:
            continue
        if role == "system":
            parts.append(f"[系统指令]\n{content}")
        elif role == "assistant":
            parts.append(f"[助手上一轮回答]\n{content}")
        else:
            parts.append(f"[用户]\n{content}")
    return "\n\n".join(parts)


_TAIL_PAT = re.compile(r"^(如果|若|如需|要不要|希望以上|我可以|您?需要|当然|另外)[，,]?(你|您)?(还)?(需要|想|要)")


def _strip_tail(text: str) -> str:
    """剥掉豆包回复末尾的对话式提议行（"如果你需要，我可以…"），从尾往回剔。"""
    lines = (text or "").rstrip().splitlines()
    while lines and (not lines[-1].strip() or _TAIL_PAT.search(lines[-1].strip())):
        lines.pop()
    return "\n".join(lines).rstrip()


def _completion(text: str, pt: int = 0, ct: int = 0) -> dict:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL,
        "choices": [{"index": 0,
                     "message": {"role": "assistant", "content": text},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": pt, "completion_tokens": ct,
                  "total_tokens": pt + ct},
    }


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [
        {"id": MODEL, "object": "model", "created": 0, "owned_by": "doubao-app-uia"}]}


@app.get("/healthz")
async def healthz():
    return {"ok": True, "model": MODEL}


@app.post("/v1/chat/completions")
async def chat(req: Request):
    body = await req.json()
    prompt = _flatten(body.get("messages", []))
    if not prompt.strip():
        return JSONResponse(status_code=400, content={"error": {"message": "messages 为空"}})
    timeout = int(body.get("timeout", 300)) if str(body.get("timeout", "")).isdigit() else 300

    async with _lock:
        t0 = time.time()
        try:
            text = _strip_tail(await asyncio.to_thread(ask, prompt, timeout))
        except DoubaoError as e:
            metrics_log.record(source="bridge", channel=MODEL, ok=0,
                               latency_ms=int((time.time() - t0) * 1000), prompt_chars=len(prompt),
                               cost_class=COST_CLASS, note=_note(e))
            return JSONResponse(status_code=502, content={"error": {"message": f"doubao bridge: {e}"}})
    pt, ct = cost.estimate_tokens(len(prompt), len(text))
    actual = cost.actual_cost_cny(MODEL, pt, ct)
    saved = cost.saved_cny(MODEL, pt, ct)
    metrics_log.record(source="bridge", channel=MODEL, ok=1,
                       latency_ms=int((time.time() - t0) * 1000), prompt_chars=len(prompt),
                       answer_chars=len(text), prompt_tokens=pt, completion_tokens=ct,
                       cost_class=COST_CLASS, cost_usd=cost.to_usd(actual),
                       saved_usd=cost.to_usd(saved),
                       note=f"ok; saved≈¥{saved:.2f}")

    if body.get("stream"):  # 简化流式：整段一次性吐出
        cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        chunk = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                 "model": MODEL,
                 "choices": [{"index": 0, "delta": {"role": "assistant", "content": text},
                              "finish_reason": None}]}
        done = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                "model": MODEL,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}

        async def gen():
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return _completion(text, pt, ct)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=4301, log_level="warning")
