# -*- coding: utf-8 -*-
# WorkBuddy App 执行器 → OpenAI 兼容接口 适配桥（127.0.0.1:4303）
# 对外：POST /v1/chat/completions（模型名 workbuddy-app），内部经执行器驱动 WorkBuddy 桌面端
# 花的是 WorkBuddy 套餐/免费额度积分，零 API Key；可挂进 LiteLLM 网关当档位
# 启动：.\start-workbuddy.ps1（复用 ai-relay\.venv）
from workbuddy_executor_cdp import WorkBuddyError, ask
import asyncio
import json
import time
import uuid

import metrics_log

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

MODEL = "workbuddy-app"
COST_CLASS = "free_app"
_LIMIT_HINT = ("限流", "已用完", "速率", "rate limit", "too many", "quota", "额度", "积分不足")
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


def _completion(text: str) -> dict:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL,
        "choices": [{"index": 0,
                     "message": {"role": "assistant", "content": text},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [
        {"id": MODEL, "object": "model", "created": 0, "owned_by": "workbuddy-app-cdp"}]}


@app.get("/healthz")
async def healthz():
    return {"ok": True, "model": MODEL}


@app.post("/v1/chat/completions")
async def chat(req: Request):
    body = await req.json()
    prompt = _flatten(body.get("messages", []))
    if not prompt.strip():
        return JSONResponse(status_code=400, content={"error": {"message": "messages 为空"}})
    timeout = int(body.get("timeout", 420)) if str(body.get("timeout", "")).isdigit() else 420

    async with _lock:
        t0 = time.time()
        try:
            text = await asyncio.to_thread(ask, prompt, timeout)
        except WorkBuddyError as e:
            metrics_log.record(source="bridge", channel=MODEL, ok=0,
                               latency_ms=int((time.time() - t0) * 1000), prompt_chars=len(prompt),
                               cost_class=COST_CLASS, note=_note(e))
            return JSONResponse(status_code=502, content={"error": {"message": f"workbuddy bridge: {e}"}})
    metrics_log.record(source="bridge", channel=MODEL, ok=1,
                       latency_ms=int((time.time() - t0) * 1000), prompt_chars=len(prompt),
                       answer_chars=len(text), cost_class=COST_CLASS, note="ok")

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

    return _completion(text)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=4303, log_level="warning")
