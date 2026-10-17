# -*- coding: utf-8 -*-
# Kimi Code 会员额度 → OpenAI 兼容接口 适配桥
# 上游：kimi web 本地服务（REST，默认 127.0.0.1:58628，花的是 Kimi Code 套餐额度，不走 API Key）
# 对外：POST /v1/chat/completions（OpenAI 兼容，支持 stream / 非 stream）
# 启动：由 start-kimi-vip.ps1 拉起；依赖 ai-relay/.venv 里的 fastapi/uvicorn/httpx
import asyncio
import json
import time
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

ROOT = Path(__file__).resolve().parent
KIMI_BASE = "http://127.0.0.1:58628"
TOKEN_FILE = ROOT / ".kimi-web-token"
SESSION_CWD = "c:/kimi/修仙与地下城"  # 已信任的工作区，桥接会话都建在这里
TURN_TIMEOUT = 600  # 单次回答最长等待（秒）

# 对外模型名 → kimi web 模型 ID
MODEL_MAP = {
    "kimi-vip": "kimi-code/kimi-for-coding",            # K2.8 Preview，1M 上下文
    "kimi-vip-hs": "kimi-code/kimi-for-coding-highspeed",  # K2.7 Code 高速版，256k
}

# 桥接定位：给 WorkBuddy 等「客户端型 Agent」当纯 LLM 用，禁止它自己跑工具
BRIDGE_PREFIX = (
    "（本请求来自 API 桥接层，请作为纯语言模型直接回答，"
    "不要读取/修改文件、不要运行命令、不要调用任何工具。）\n\n"
)

app = FastAPI()


def _token() -> str:
    return TOKEN_FILE.read_text(encoding="utf-8-sig").strip()


def _headers() -> dict:
    return {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json; charset=utf-8"}


def _flatten(messages: list) -> str:
    """把 OpenAI messages 压成单条 prompt 文本（保留多轮历史）。"""
    parts = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list):  # 多模态消息只取文本段
            content = "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
        if not content:
            continue
        if role == "system":
            parts.append(f"[系统指令]\n{content}")
        elif role == "assistant":
            parts.append(f"[助手上一轮回答]\n{content}")
        else:
            parts.append(f"[用户]\n{content}")
    return BRIDGE_PREFIX + "\n\n".join(parts)


async def _run_turn(client: httpx.AsyncClient, prompt: str, model_id: str) -> str:
    """建会话 → 发 prompt → 轮询到空闲 → 取助手回复 → 归档会话。"""
    r = await client.post(
        f"{KIMI_BASE}/api/v1/sessions",
        headers=_headers(),
        json={"metadata": {"cwd": SESSION_CWD}},
        timeout=30,
    )
    r.raise_for_status()
    sid = r.json()["data"]["id"]
    try:
        r = await client.post(
            f"{KIMI_BASE}/api/v1/sessions/{sid}/prompts",
            headers=_headers(),
            json={
                "model": model_id,
                "permission_mode": "auto",
                "content": [{"type": "text", "text": prompt}],
            },
            timeout=30,
        )
        r.raise_for_status()

        deadline = time.time() + TURN_TIMEOUT
        while time.time() < deadline:
            await asyncio.sleep(3)
            r = await client.get(f"{KIMI_BASE}/api/v1/sessions/{sid}", headers=_headers(), timeout=30)
            data = r.json()["data"]
            if not data["busy"]:
                if data.get("last_turn_reason") not in (None, "completed"):
                    raise RuntimeError(f"kimi turn 失败: {data.get('last_turn_reason')}")
                break
        else:
            raise TimeoutError(f"等待 kimi 回答超过 {TURN_TIMEOUT}s")

        r = await client.get(
            f"{KIMI_BASE}/api/v1/sessions/{sid}/messages?page_size=50",
            headers=_headers(),
            timeout=30,
        )
        items = r.json()["data"]["items"]
        # 取最后一条助手消息的文本段（跳过 thinking）
        for msg in reversed(items):
            if msg.get("role") == "assistant":
                texts = [c.get("text", "") for c in msg.get("content", []) if c.get("type") == "text" and c.get("text")]
                if texts:
                    return "\n".join(texts)
        raise RuntimeError("kimi 未返回助手文本")
    finally:
        try:  # 归档保持会话列表干净，失败不影响主流程
            await client.post(f"{KIMI_BASE}/api/v1/sessions/{sid}:archive", headers=_headers(), timeout=10)
        except Exception:
            pass


def _completion(model: str, text: str) -> dict:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [
        {"id": k, "object": "model", "created": 0, "owned_by": "kimi-code-oauth"} for k in MODEL_MAP
    ]}


@app.post("/v1/chat/completions")
async def chat(req: Request):
    body = await req.json()
    model = body.get("model", "kimi-vip")
    model_id = MODEL_MAP.get(model, MODEL_MAP["kimi-vip"])
    prompt = _flatten(body.get("messages", []))
    if not prompt.strip():
        return JSONResponse(status_code=400, content={"error": {"message": "messages 为空"}})

    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            text = await _run_turn(client, prompt, model_id)
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": {"message": f"kimi bridge: {e}"}})

    if body.get("stream"):  # 简化流式：整段一次性吐出
        cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        chunk = {
            "id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}],
        }
        done = {
            "id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }

        async def gen():
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return _completion(model, text)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=4100, log_level="warning")
