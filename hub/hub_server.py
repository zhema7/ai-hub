# -*- coding: utf-8 -*-
# AI Hub · 多智能体调度层
# 一个入口发布任务，两种执行通道：
#   kind=cli → 派遣 kimi CLI 执行器（花 Kimi Code 会员订阅，可读写文件/跑命令）
#   kind=llm → 调中转站网关档位（kimi-vip 走会员；cheap/coding/think/long 走开放平台 Key）
# 任务记录存 SQLite（data/hub.db），CLI 原始事件流存 data/logs/<id>.jsonl
import asyncio
import json
import os
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
RELAY = ROOT.parent / "ai-relay"
DATA = ROOT / "data"
LOGS = DATA / "logs"
LOGS.mkdir(parents=True, exist_ok=True)
DB = DATA / "hub.db"

GATEWAY_BASE = "http://127.0.0.1:4000/v1"
GATEWAY_HEALTH = "http://127.0.0.1:4000/health/liveliness"
BRIDGE_MODELS = "http://127.0.0.1:4100/v1/models"
LLM_TIMEOUT = 600          # LLM 任务单次请求上限（秒）
CLI_TIMEOUT = 3600         # CLI 任务总时长上限（秒）
WORKERS = 3                # 并发任务数，其余排队

LLM_TARGETS = {
    "kimi-vip":    {"desc": "会员订阅 · K2.8 1M（经适配桥）", "needs": None},
    "kimi-vip-hs": {"desc": "会员订阅 · K2.7 高速 256k（经适配桥）", "needs": None},
    "cheap":       {"desc": "API Key · 轻量档（豆包 lite）", "needs": "ARK_API_KEY"},
    "coding":      {"desc": "API Key · 编程档（Kimi K2.7 / 豆包 Evolving）", "needs": "MOONSHOT_API_KEY"},
    "think":       {"desc": "API Key · 推理档（Kimi K3 / 豆包 Pro）", "needs": "MOONSHOT_API_KEY"},
    "long":        {"desc": "API Key · 长上下文档（1M）", "needs": "MOONSHOT_API_KEY"},
}


def _load_env_file() -> dict:
    env = {}
    f = RELAY / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


_ENV_FILE = _load_env_file()
MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY") or _ENV_FILE.get("LITELLM_MASTER_KEY", "")
KIMI_EXE = shutil.which("kimi") or r"C:\Users\roy18\.kimi-code\bin\kimi.exe"

# ---------------- SQLite ----------------
_lock = threading.Lock()
conn = sqlite3.connect(DB, check_same_thread=False)
conn.row_factory = sqlite3.Row
conn.execute("""
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    name TEXT,
    kind TEXT NOT NULL,          -- cli | llm
    target TEXT NOT NULL,        -- kimi-cli | 网关档位名
    model TEXT,                  -- CLI 可选模型别名（-m）
    prompt TEXT NOT NULL,
    cwd TEXT,
    status TEXT NOT NULL,        -- queued | running | done | error | cancelled
    result TEXT,
    error TEXT,
    session_id TEXT,             -- CLI 会话 ID（可 kimi -r 续聊）
    created_at REAL, started_at REAL, finished_at REAL
)""")


def db(sql: str, args=(), one=False):
    with _lock:
        cur = conn.execute(sql, args)
        conn.commit()
        if sql.lstrip().upper().startswith("SELECT"):
            rows = cur.fetchall()
            return (rows[0] if rows else None) if one else rows
        return cur


class Cancelled(Exception):
    pass


# ---------------- 执行器 ----------------
def clean_env() -> dict:
    """子进程环境：必须清掉残留的 7993 代理变量，否则 OAuth/网关全被截胡。"""
    env = dict(os.environ)
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(k, None)
    env["NO_PROXY"] = "127.0.0.1,localhost"
    return env


async def run_llm(row) -> str:
    async with httpx.AsyncClient(trust_env=False, timeout=LLM_TIMEOUT) as client:
        r = await client.post(
            f"{GATEWAY_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {MASTER_KEY}"},
            json={"model": row["target"],
                  "messages": [{"role": "user", "content": row["prompt"]}]},
        )
    if r.status_code != 200:
        raise RuntimeError(f"网关 {r.status_code}: {r.text[:300]}")
    return r.json()["choices"][0]["message"]["content"]


running_procs: dict = {}  # task_id -> asyncio Process（用于取消）


async def run_cli(tid: str, row):
    cmd = [KIMI_EXE, "-p", row["prompt"], "--output-format", "stream-json"]
    if row["model"]:
        cmd += ["-m", row["model"]]
    cwd = row["cwd"] if row["cwd"] and Path(row["cwd"]).is_dir() else str(ROOT.parent)
    log_path = LOGS / f"{tid}.jsonl"

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
        env=clean_env(),
    )
    running_procs[tid] = proc
    parts, session_id = [], None
    try:
        with open(log_path, "w", encoding="utf-8") as log:
            async def pump():
                nonlocal session_id
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        return
                    s = line.decode("utf-8", "replace").strip()
                    if not s:
                        continue
                    log.write(s + "\n")
                    log.flush()
                    try:
                        ev = json.loads(s)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("role") == "assistant" and ev.get("content"):
                        parts.append(ev["content"])
                    elif ev.get("role") == "meta" and ev.get("type") == "session.resume_hint":
                        session_id = ev.get("session_id")

            await asyncio.wait_for(pump(), CLI_TIMEOUT)
            rc = await proc.wait()
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"CLI 任务超过 {CLI_TIMEOUT}s，已终止")
    finally:
        running_procs.pop(tid, None)

    if db("SELECT status FROM tasks WHERE id=?", (tid,), one=True)["status"] == "cancelled":
        raise Cancelled()
    if rc != 0 and not parts:
        raise RuntimeError(f"kimi CLI 退出码 {rc}，无回答输出，详见日志")
    return ("\n\n".join(parts[-1:]) if parts else "", session_id)


# ---------------- 调度 ----------------
app = FastAPI(title="AI Hub")
queue: asyncio.Queue = None  # noqa: 在 startup 里绑定当前事件循环


async def run_task(tid: str):
    row = db("SELECT * FROM tasks WHERE id=?", (tid,), one=True)
    if not row or row["status"] != "queued":
        return  # 已被取消
    db("UPDATE tasks SET status='running', started_at=? WHERE id=?", (time.time(), tid))
    try:
        if row["kind"] == "cli":
            result, session_id = await run_cli(tid, row)
        else:
            result, session_id = await run_llm(row), None
        db("""UPDATE tasks SET status='done', result=?, session_id=?, finished_at=?
              WHERE id=? AND status='running'""",
           (result, session_id, time.time(), tid))
    except Cancelled:
        pass  # cancel 接口已写好状态
    except Exception as e:
        db("""UPDATE tasks SET status='error', error=?, finished_at=?
              WHERE id=? AND status='running'""",
           (str(e), time.time(), tid))


async def worker():
    while True:
        tid = await queue.get()
        try:
            await run_task(tid)
        finally:
            queue.task_done()


@app.on_event("startup")
async def startup():
    global queue
    queue = asyncio.Queue()
    # 服务重启：上次中断的任务标记为 error
    db("""UPDATE tasks SET status='error', error='Hub 服务重启，任务中断', finished_at=?
          WHERE status IN ('queued','running')""", (time.time(),))
    for _ in range(WORKERS):
        asyncio.create_task(worker())


# ---------------- API ----------------
class TaskIn(BaseModel):
    kind: str                    # cli | llm
    prompt: str
    target: str = ""             # llm 必填：档位名；cli 固定 kimi-cli
    name: str = ""
    model: str = ""              # cli 可选：-m 模型别名
    cwd: str = ""                # cli 可选：工作目录


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/health")
async def health():
    out = {"gateway": False, "bridge": False, "kimi_cli": Path(KIMI_EXE).exists(),
           "master_key": bool(MASTER_KEY)}
    async with httpx.AsyncClient(trust_env=False, timeout=3) as client:
        for name, url in (("gateway", GATEWAY_HEALTH), ("bridge", BRIDGE_MODELS)):
            try:
                r = await client.get(url)
                out[name] = r.status_code < 500
            except Exception:
                pass
    return out


@app.get("/api/targets")
def targets():
    return {
        "cli": [{"id": "kimi-cli", "desc": "Kimi Code 会员订阅 · 可读写文件/跑命令",
                 "available": Path(KIMI_EXE).exists()}],
        "llm": [{"id": k, "desc": v["desc"],
                 "available": (not v["needs"]) or bool(_ENV_FILE.get(v["needs"]))}
                for k, v in LLM_TARGETS.items()],
    }


@app.post("/api/tasks")
def create_task(t: TaskIn):
    if t.kind not in ("cli", "llm"):
        raise HTTPException(400, "kind 必须是 cli 或 llm")
    if not t.prompt.strip():
        raise HTTPException(400, "prompt 不能为空")
    if t.kind == "llm":
        if t.target not in LLM_TARGETS:
            raise HTTPException(400, f"未知档位 {t.target}，可选：{list(LLM_TARGETS)}")
    else:
        t.target = "kimi-cli"
        if not Path(KIMI_EXE).exists():
            raise HTTPException(503, "未找到 kimi CLI")
    tid = uuid.uuid4().hex[:12]
    db("""INSERT INTO tasks (id,name,kind,target,model,prompt,cwd,status,created_at)
          VALUES (?,?,?,?,?,?,?,'queued',?)""",
       (tid, t.name.strip(), t.kind, t.target, t.model.strip(), t.prompt,
        t.cwd.strip(), time.time()))
    queue.put_nowait(tid)
    return {"id": tid}


@app.get("/api/tasks")
def list_tasks(limit: int = 100):
    rows = db("""SELECT id,name,kind,target,model,status,error,session_id,
                        substr(prompt,1,120) AS prompt_head,
                        length(result) AS result_len,
                        created_at,started_at,finished_at
                 FROM tasks ORDER BY created_at DESC LIMIT ?""", (limit,))
    return [dict(r) for r in rows]


@app.get("/api/tasks/{tid}")
def get_task(tid: str):
    row = db("SELECT * FROM tasks WHERE id=?", (tid,), one=True)
    if not row:
        raise HTTPException(404, "任务不存在")
    return dict(row)


@app.get("/api/tasks/{tid}/log")
def get_log(tid: str, tail: int = 30):
    p = LOGS / f"{tid}.jsonl"
    if not p.exists():
        return {"lines": []}
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return {"lines": lines[-tail:]}


@app.post("/api/tasks/{tid}/cancel")
def cancel_task(tid: str):
    row = db("SELECT status FROM tasks WHERE id=?", (tid,), one=True)
    if not row:
        raise HTTPException(404, "任务不存在")
    if row["status"] not in ("queued", "running"):
        return {"ok": False, "msg": f"任务已是 {row['status']} 状态"}
    db("UPDATE tasks SET status='cancelled', finished_at=? WHERE id=?", (time.time(), tid))
    proc = running_procs.get(tid)
    if proc and proc.returncode is None:
        proc.kill()
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=4200, log_level="warning")
