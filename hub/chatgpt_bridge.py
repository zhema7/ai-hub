# -*- coding: utf-8 -*-
# ChatGPT 桌面端执行器 → OpenAI 兼容接口 适配桥（127.0.0.1:4302）
# 对外：POST /v1/chat/completions（模型名 chatgpt-app），内部经 chatgpt_executor 以 CDP 驱动桌面端
# 花的是 ChatGPT 订阅额度，零 API Key；可挂进 LiteLLM 网关当档位 / 路由表当通道
# 启动：.\start-chatgpt.ps1（复用 ai-relay\.venv；ChatGPT 须带 --remote-debugging-port=9222）
# 注意：CDP 单实例，全局锁串行；全程 Runtime.evaluate 注入，不抢前台焦点
import asyncio
import json
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from chatgpt_executor import ChatGPTError, ask
import asyncio
import json
import time
import uuid

import metrics_log

MODEL = "chatgpt-app"
COST_CLASS = "member"
_LIMIT_HINT = ("限流", "已用完", "速率", "rate limit", "too many", "quota")
app = FastAPI()
_lock = asyncio.Lock()          # CDP 页面单实例，所有请求串行


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
        {"id": MODEL, "object": "model", "created": 0, "owned_by": "chatgpt-app-cdp"}]}


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
            text = await asyncio.to_thread(ask, prompt, timeout)
        except ChatGPTError as e:
            metrics_log.record(source="bridge", channel=MODEL, ok=0,
                               latency_ms=int((time.time() - t0) * 1000), prompt_chars=len(prompt),
                               cost_class=COST_CLASS, note=_note(e))
            return JSONResponse(status_code=502, content={"error": {"message": f"chatgpt bridge: {e}"}})
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
    uvicorn.run(app, host="127.0.0.1", port=4302, log_level="warning")
