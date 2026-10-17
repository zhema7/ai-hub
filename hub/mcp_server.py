# -*- coding: utf-8 -*-
# AI Hub · MCP Server Facade（反向 MCP）
# 把任务板暴露成 MCP 服务，封闭 GUI 平台（Trae IDE / WorkBuddy / Kimi Work）
# 以 MCP 客户端身份连入，认领任务、回写结果与产物。
#
# 传输（一个端口 4300）：
#   POST/GET /mcp   Streamable HTTP（Trae IDE 等现代客户端）
#   GET      /sse   旧版 HTTP+SSE（WorkBuddy 自定义连接器 type=sse）
#   POST     /messages/  SSE 上行通道
# 附带最小 REST（Hub 侧建单/查单用）：
#   POST /api/tasks {title, instructions}
#   GET  /api/tasks[/id]     GET /healthz
#
# 启动：.\start-mcp.ps1（复用 ai-relay\.venv，mcp<2）
import json
import sqlite3
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi.middleware.cors import CORSMiddleware
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
ARTS = DATA / "artifacts"
ARTS.mkdir(parents=True, exist_ok=True)
DB = DATA / "board.db"

# ---------------- 任务板存储 ----------------
_lock = threading.Lock()
_conn = sqlite3.connect(DB, check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.execute("""CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    instructions TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',   -- open | claimed | done | failed
    claimed_by TEXT, result TEXT, error TEXT,
    created_at REAL, updated_at REAL)""")
_conn.execute("""CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT, name TEXT, path TEXT, note TEXT, created_at REAL)""")


def db(sql: str, args=(), one=False):
    with _lock:
        cur = _conn.execute(sql, args)
        _conn.commit()
        if sql.lstrip().upper().startswith("SELECT"):
            rows = cur.fetchall()
            return (rows[0] if rows else None) if one else rows
        return cur


def _task_full(row) -> dict:
    d = dict(row)
    arts = db("SELECT name, path, note FROM artifacts WHERE task_id=? ORDER BY id", (d["id"],))
    d["artifacts"] = [dict(a) for a in arts]
    return d


def _safe_name(name: str) -> str:
    return name.replace("\\", "_").replace("/", "_").replace("..", "_").strip() or "artifact.txt"


# ---------------- MCP 工具（平台客户端调用） ----------------
mcp = FastMCP(
    "ai-hub-board",
    instructions=(
        "这是 AI Hub 的共享任务板。典型协作流程："
        "task_list 找 open 任务 → task_claim 原子认领（认领即拿到完整指令）→ "
        "按指令干活 → artifact_write 把产物落账本 → task_report 汇报 done/failed。"
        "产物一律走 artifact_write 落盘回传引用，不要只把长文本塞进回话里。"
    ),
    host="127.0.0.1",
    port=4300,
)


@mcp.tool()
def task_list(status: str = "") -> dict:
    """列出任务板任务。status 可选：open / claimed / done / failed，留空返回全部（新→旧）。"""
    if status:
        rows = db("SELECT id,title,status,claimed_by,created_at,updated_at FROM tasks WHERE status=? ORDER BY created_at DESC", (status,))
    else:
        rows = db("SELECT id,title,status,claimed_by,created_at,updated_at FROM tasks ORDER BY created_at DESC")
    return {"count": len(rows), "tasks": [dict(r) for r in rows]}


@mcp.tool()
def task_claim(task_id: str, client_name: str) -> dict:
    """原子认领一个 open 状态的任务。认领成功即返回完整指令 instructions。"""
    cur = db("UPDATE tasks SET status='claimed', claimed_by=?, updated_at=? WHERE id=? AND status='open'",
             (client_name, time.time(), task_id))
    if cur.rowcount == 0:
        row = db("SELECT status, claimed_by FROM tasks WHERE id=?", (task_id,), one=True)
        if not row:
            return {"ok": False, "msg": f"任务 {task_id} 不存在"}
        return {"ok": False, "msg": f"任务已被认领或已完成（当前 {row['status']} by {row['claimed_by']}）"}
    row = db("SELECT * FROM tasks WHERE id=?", (task_id,), one=True)
    return {"ok": True, "task": _task_full(row)}


@mcp.tool()
def task_report(task_id: str, status: str, result_summary: str = "", error: str = "") -> dict:
    """回写任务结果。status 只能是 done / failed；done 填 result_summary，failed 填 error。"""
    if status not in ("done", "failed"):
        return {"ok": False, "msg": "status 只能是 done 或 failed"}
    db("UPDATE tasks SET status=?, result=?, error=?, updated_at=? WHERE id=?",
       (status, result_summary, error, time.time(), task_id))
    return {"ok": True, "task_id": task_id, "status": status}


@mcp.tool()
def artifact_write(task_id: str, name: str, content: str, note: str = "") -> dict:
    """把任务产物（文本内容）写入账本并登记。产物外置：客户端之间只传引用不传全文。"""
    name = _safe_name(name)
    tdir = ARTS / task_id
    tdir.mkdir(parents=True, exist_ok=True)
    path = tdir / name
    path.write_text(content, encoding="utf-8")
    db("INSERT INTO artifacts (task_id,name,path,note,created_at) VALUES (?,?,?,?,?)",
       (task_id, name, str(path), note, time.time()))
    return {"ok": True, "name": name, "path": str(path), "bytes": len(content.encode("utf-8"))}


@mcp.tool()
def artifact_read(task_id: str, name: str) -> dict:
    """按名称读取某任务的产物内容。"""
    name = _safe_name(name)
    path = ARTS / task_id / name
    if not path.exists():
        return {"ok": False, "msg": f"产物 {name} 不存在"}
    return {"ok": True, "name": name, "content": path.read_text(encoding="utf-8", errors="replace")}


@mcp.tool()
def artifact_list(task_id: str = "") -> dict:
    """列出产物登记记录；task_id 留空列出全部。"""
    if task_id:
        rows = db("SELECT task_id,name,note,created_at FROM artifacts WHERE task_id=? ORDER BY id", (task_id,))
    else:
        rows = db("SELECT task_id,name,note,created_at FROM artifacts ORDER BY id DESC LIMIT 200")
    return {"count": len(rows), "artifacts": [dict(r) for r in rows]}


@mcp.tool()
def task_get(task_id: str) -> dict:
    """查看任务详情（含状态、结果、产物清单）。"""
    row = db("SELECT * FROM tasks WHERE id=?", (task_id,), one=True)
    if not row:
        return {"ok": False, "msg": f"任务 {task_id} 不存在"}
    return {"ok": True, "task": _task_full(row)}


# ---------------- REST（Hub 侧建单/查单） ----------------
class TaskIn(BaseModel):
    title: str
    instructions: str


async def rest_create(req: Request):
    body = await req.json()
    title, ins = (body.get("title") or "").strip(), (body.get("instructions") or "").strip()
    if not title or not ins:
        return JSONResponse(status_code=400, content={"error": "title / instructions 必填"})
    tid = uuid.uuid4().hex[:10]
    db("INSERT INTO tasks (id,title,instructions,status,created_at,updated_at) VALUES (?,?,?,'open',?,?)",
       (tid, title, ins, time.time(), time.time()))
    return JSONResponse({"id": tid})


async def rest_list(req: Request):
    rows = db("SELECT * FROM tasks ORDER BY created_at DESC LIMIT 200")
    return JSONResponse([dict(r) for r in rows])


async def rest_get(req: Request):
    tid = req.path_params["tid"]
    row = db("SELECT * FROM tasks WHERE id=?", (tid,), one=True)
    if not row:
        return JSONResponse(status_code=404, content={"error": "任务不存在"})
    return JSONResponse(_task_full(row))


async def healthz(req: Request):
    n = db("SELECT COUNT(*) c FROM tasks WHERE status='open'", one=True)["c"]
    return JSONResponse({"ok": True, "open_tasks": n})


# ---------------- 组合应用：/mcp + /sse + /api ----------------
http_app = mcp.streamable_http_app()   # 内部路由：/mcp
sse_app = mcp.sse_app()                # 内部路由：/sse + /messages/

routes = list(http_app.routes) + list(sse_app.routes) + [
    Route("/api/tasks", rest_create, methods=["POST"]),
    Route("/api/tasks", rest_list, methods=["GET"]),
    Route("/api/tasks/{tid}", rest_get, methods=["GET"]),
    Route("/healthz", healthz, methods=["GET"]),
]

app = Starlette(routes=routes, lifespan=http_app.router.lifespan_context)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=4300, log_level="warning")
