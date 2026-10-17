# -*- coding: utf-8 -*-
# 豆包 App 执行器 → OpenAI 兼容接口 适配桥（127.0.0.1:4301）
# 对外：POST /v1/chat/completions（模型名 doubao-app），内部经 doubao_executor 驱动豆包桌面端
# 花的是豆包 App 会员/免费额度，零 API Key；可挂进 LiteLLM 网关当档位
# 启动：.\start-doubao.ps1（复用 ai-relay\.venv）
# 注意：GUI 单例，全局锁串行；打字会短暂抢前台焦点（send-input 硬要求）
import asyncio
import json
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from doubao_executor import DoubaoError, ask

MODEL = "doubao-app"
app = FastAPI()
_lock = asyncio.Lock()          # GUI 只有一个，所有请求串行


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
        try:
            text = await asyncio.to_thread(ask, prompt, timeout)
        except DoubaoError as e:
            return JSONResponse(status_code=502, content={"error": {"message": f"doubao bridge: {e}"}})

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
    uvicorn.run(app, host="127.0.0.1", port=4301, log_level="warning")
