# -*- coding: utf-8 -*-
# AI Hub · MCP Server Facade（反向 MCP · P3 看板版）
# 把任务板暴露成 MCP 服务，封闭 GUI 平台（Trae IDE / WorkBuddy / Kimi Work）
# 以 MCP 客户端身份连入，认领任务、回写结果与产物。
#
# P3 看板升级：
#   状态机  blocked → open(backlog) → doing → review → done / failed
#           review 打回 → doing（带 feedback 回流）
#   任务即数据：parent_id 子任务 / depends_on 依赖阻塞 / project 项目分组
#   Spec 共享：项目级文档经 spec_write/spec_read 在智能体间传上下文
#   kind/priority 字段为 P4 能力路由预留
#
# 传输（一个端口 4300）：
#   POST/GET /mcp   Streamable HTTP（Trae IDE 等现代客户端）
#   GET      /sse   旧版 HTTP+SSE（WorkBuddy 自定义连接器 type=sse）
#   POST     /messages/  SSE 上行通道
# 附带 REST（Hub 侧建单/查单/看板/指标/观测）：
#   POST /api/tasks {title, instructions, project?, parent_id?, depends_on?, priority?, kind?, output_dir?}
#   GET  /api/tasks[/id]     GET /api/board?project=     GET /healthz
#   POST /api/metrics {source,...}   任意客户端自报用量（也接受数组）
#   GET  /dashboard                 用量与效果看板（统一指标表 + 任务 + 工作舱记忆，30s 自刷新）
#
# P7 工作舱记忆工具：memory_write / memory_read / memory_list / memory_forget
#   （每项目一个 workspaces/<project>/memory.db，SQLite FTS5 中文子串检索，跨客户端共享）
#
# 启动：.\start-mcp.ps1（hub\.venv，mcp<2——与 ai-relay 的 mcp 2.x 互斥）
import json
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from fastapi.middleware.cors import CORSMiddleware
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

import dashboard
import memory_store
import metrics_log

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
ARTS = DATA / "artifacts"
SPECS_LEGACY = ARTS / "_specs"      # P3 旧 Spec 位置（读兜底 + 一次性迁移到工作舱）
ARTS.mkdir(parents=True, exist_ok=True)
DB = DATA / "board.db"

# 看板列（存储状态 → 展示列）；open 即 backlog
COLUMNS = ["blocked", "open", "doing", "review", "done", "failed"]

# ---------------- 任务板存储 ----------------
_lock = threading.Lock()
_conn = sqlite3.connect(DB, check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.execute("""CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    instructions TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',   -- blocked | open | doing | review | done | failed
    claimed_by TEXT, result TEXT, error TEXT,
    created_at REAL, updated_at REAL)""")
_conn.execute("""CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT, name TEXT, path TEXT, note TEXT, created_at REAL)""")


def _migrate():
    """就地加列 + 旧状态迁移（claimed→doing），旧库无损升级。"""
    cols = {r[1] for r in _conn.execute("PRAGMA table_info(tasks)")}
    adds = {
        "parent_id": "TEXT DEFAULT ''",
        "depends_on": "TEXT DEFAULT '[]'",   # JSON 数组，任务 id 列表
        "project": "TEXT DEFAULT 'default'",
        "kind": "TEXT DEFAULT 'task'",       # P4 能力路由预留
        "priority": "INTEGER DEFAULT 0",
        "feedback": "TEXT DEFAULT ''",       # review 回流意见
        "output_dir": "TEXT DEFAULT ''",     # 产物直落目录（如 Trae 项目文件夹）
    }
    for c, t in adds.items():
        if c not in cols:
            _conn.execute(f"ALTER TABLE tasks ADD COLUMN {c} {t}")
    _conn.execute("UPDATE tasks SET status='doing' WHERE status='claimed'")
    _conn.commit()


def _migrate_metrics():
    """P6：统一指标表（就地迁移，看板只读这一张 + tasks）。"""
    metrics_log.ensure(_conn)
    _conn.commit()


def _migrate_specs():
    """P7：P3 旧 _specs/<project>/*.md → 工作舱 workspaces/<project>/spec/（一次性，旧库无损）。"""
    if not SPECS_LEGACY.is_dir():
        return
    for pdir in SPECS_LEGACY.iterdir():
        if not pdir.is_dir():
            continue
        dst = memory_store.spec_dir(pdir.name)
        for f in pdir.glob("*.md"):
            t = dst / f.name
            if not t.exists():
                try:
                    shutil.copy2(f, t)
                except Exception as e:
                    print(f"[spec-migrate] {f} -> {t} 失败: {e}")


_migrate()
_migrate_metrics()
_migrate_specs()


def db(sql: str, args=(), one=False):
    with _lock:
        cur = _conn.execute(sql, args)
        _conn.commit()
        if sql.lstrip().upper().startswith("SELECT"):
            rows = cur.fetchall()
            return (rows[0] if rows else None) if one else rows
        return cur


def _deps_of(row) -> list:
    try:
        return json.loads(row["depends_on"] or "[]")
    except Exception:
        return []


def _refresh_blocks():
    """依赖全部 done 的 blocked 任务自动解锁为 open。"""
    rows = db("SELECT id, depends_on FROM tasks WHERE status='blocked'")
    if not rows:
        return
    done_ids = {r["id"] for r in db("SELECT id FROM tasks WHERE status='done'")}
    for r in rows:
        if all(d in done_ids for d in _deps_of(r)):
            db("UPDATE tasks SET status='open', updated_at=? WHERE id=?", (time.time(), r["id"]))


def _task_full(row) -> dict:
    d = dict(row)
    d["depends_on"] = _deps_of(row)
    arts = db("SELECT name, path, note FROM artifacts WHERE task_id=? ORDER BY id", (d["id"],))
    d["artifacts"] = [dict(a) for a in arts]
    d["subtasks"] = [dict(s) for s in db(
        "SELECT id,title,status,claimed_by FROM tasks WHERE parent_id=? ORDER BY created_at", (d["id"],))]
    return d


def _safe_name(name: str) -> str:
    return name.replace("\\", "_").replace("/", "_").replace("..", "_").strip() or "artifact.txt"


def _create_task(title: str, instructions: str, project: str = "default",
                 parent_id: str = "", depends_on=None, priority: int = 0, kind: str = "task",
                 output_dir: str = "") -> dict:
    deps = [d.strip() for d in depends_on.split(",") if d.strip()] if isinstance(depends_on, str) else list(depends_on or [])
    if parent_id and not db("SELECT id FROM tasks WHERE id=?", (parent_id,), one=True):
        return {"ok": False, "msg": f"父任务 {parent_id} 不存在"}
    for d in deps:
        if not db("SELECT id FROM tasks WHERE id=?", (d,), one=True):
            return {"ok": False, "msg": f"依赖任务 {d} 不存在"}
    done_ids = {r["id"] for r in db("SELECT id FROM tasks WHERE status='done'")}
    status = "blocked" if any(d not in done_ids for d in deps) else "open"
    tid = uuid.uuid4().hex[:10]
    db("""INSERT INTO tasks (id,title,instructions,status,parent_id,depends_on,project,kind,priority,output_dir,created_at,updated_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
       (tid, title, instructions, status, parent_id, json.dumps(deps, ensure_ascii=False),
        project or "default", kind or "task", int(priority or 0), (output_dir or "").strip(),
        time.time(), time.time()))
    return {"ok": True, "id": tid, "status": status, "blocked_by": deps if status == "blocked" else []}


# ---------------- MCP 工具（平台客户端调用） ----------------
mcp = FastMCP(
    "ai-hub-board",
    instructions=(
        "这是 AI Hub 的共享看板，多智能体跨端协作同一个项目。典型流程："
        "task_board 看全局（blocked/open/doing/review/done 五列）→ "
        "task_create 拆子任务、挂依赖（依赖未完成自动 blocked，解锁自动进 open）→ "
        "task_claim 原子认领（认领即拿到完整指令与项目 Spec 位置）→ "
        "干活前 spec_read 读项目文档对齐上下文 → artifact_write 落产物 → "
        "task_report review 交付待审 → 由 review 方 task_review 通过(done)或打回(doing, 带 feedback)。"
        "产物一律走 artifact_write 落盘回传引用，不要只把长文本塞进回话里。"
        "项目上下文走工作舱记忆：干活前 memory_read 召回核心/决策/情节记忆，重要结论用 memory_write 沉淀"
        "（core 常驻注入 / decision 决策与打回结论 / episodic 交付与踩坑）；用量与效果见 GET /dashboard。"
    ),
    host="127.0.0.1",
    port=4300,
)


@mcp.tool()
def task_board(project: str = "") -> dict:
    """按列查看看板。列：blocked(依赖未解锁) / open(可认领) / doing / review(待审) / done / failed。
    project 留空返回全部项目。"""
    _refresh_blocks()
    if project:
        rows = db("SELECT id,title,status,claimed_by,parent_id,project,kind,priority,updated_at FROM tasks WHERE project=? ORDER BY priority DESC, created_at", (project,))
    else:
        rows = db("SELECT id,title,status,claimed_by,parent_id,project,kind,priority,updated_at FROM tasks ORDER BY priority DESC, created_at")
    board = {c: [] for c in COLUMNS}
    for r in rows:
        d = dict(r)
        board.setdefault(d["status"], board["open"]).append(d)
    return {"ok": True, "project": project or "*",
            "counts": {c: len(v) for c, v in board.items()}, "board": board}


@mcp.tool()
def task_create(title: str, instructions: str, project: str = "default", parent_id: str = "",
                depends_on: str = "", priority: int = 0, kind: str = "task", output_dir: str = "") -> dict:
    """创建任务/子任务。depends_on 为逗号分隔的任务 id 列表——依赖未完成时任务为 blocked，
    依赖全部 done 后自动解锁进 open 列。parent_id 挂子任务。kind 为能力路由（coding/writing/reasoning 等）。
    output_dir 为产物直落目录（绝对路径，如 Trae 项目文件夹）：dispatcher 交付时自动把结果复制进去。"""
    return _create_task(title, instructions, project, parent_id, depends_on, priority, kind, output_dir)


@mcp.tool()
def task_subtasks(parent_id: str) -> dict:
    """列出某任务的全部子任务及其状态。"""
    rows = db("SELECT id,title,status,claimed_by,depends_on,priority,created_at FROM tasks WHERE parent_id=? ORDER BY created_at", (parent_id,))
    return {"ok": True, "count": len(rows),
            "subtasks": [dict(r, depends_on=_deps_of(r)) for r in rows]}


@mcp.tool()
def task_list(status: str = "", project: str = "") -> dict:
    """列出任务。status 可选：blocked / open / doing / review / done / failed，留空返回全部（新→旧）。"""
    _refresh_blocks()
    sql = "SELECT id,title,status,claimed_by,project,parent_id,kind,priority,created_at,updated_at FROM tasks"
    conds, args = [], []
    if status:
        conds.append("status=?"); args.append(status)
    if project:
        conds.append("project=?"); args.append(project)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    rows = db(sql + " ORDER BY created_at DESC", args)
    return {"count": len(rows), "tasks": [dict(r) for r in rows]}


@mcp.tool()
def task_claim(task_id: str, client_name: str) -> dict:
    """原子认领一个 open 状态的任务（blocked 不可认领，需等依赖解锁）。认领成功即返回完整指令。"""
    _refresh_blocks()
    cur = db("UPDATE tasks SET status='doing', claimed_by=?, updated_at=? WHERE id=? AND status='open'",
             (client_name, time.time(), task_id))
    if cur.rowcount == 0:
        row = db("SELECT status, claimed_by, depends_on FROM tasks WHERE id=?", (task_id,), one=True)
        if not row:
            return {"ok": False, "msg": f"任务 {task_id} 不存在"}
        if row["status"] == "blocked":
            return {"ok": False, "msg": f"任务被依赖阻塞，等待解锁（depends_on={_deps_of(row)}）"}
        return {"ok": False, "msg": f"任务已被认领或已流转（当前 {row['status']} by {row['claimed_by']}）"}
    row = db("SELECT * FROM tasks WHERE id=?", (task_id,), one=True)
    return {"ok": True, "task": _task_full(row)}


@mcp.tool()
def task_report(task_id: str, status: str, result_summary: str = "", error: str = "") -> dict:
    """回写任务结果。status：review（交付待审，推荐）/ done（直接完成）/ failed。
    review 后由 review 方 task_review 裁决；done/failed 填 result_summary 或 error。"""
    if status not in ("review", "done", "failed"):
        return {"ok": False, "msg": "status 只能是 review / done / failed"}
    row = db("SELECT status FROM tasks WHERE id=?", (task_id,), one=True)
    if not row:
        return {"ok": False, "msg": f"任务 {task_id} 不存在"}
    db("UPDATE tasks SET status=?, result=?, error=?, updated_at=? WHERE id=?",
       (status, result_summary, error, time.time(), task_id))
    if status == "done":
        _refresh_blocks()
    return {"ok": True, "task_id": task_id, "status": status}


@mcp.tool()
def task_review(task_id: str, approve: bool, feedback: str = "", reviewer: str = "") -> dict:
    """评审一个 review 状态的任务。approve=True → done（并解锁下游依赖）；
    approve=False → 打回 doing，feedback 回流给认领人返工。
    P7：打回结论自动沉淀为决策记忆，通过则把该单情节记忆标记 verified。"""
    row = db("SELECT status, project, title FROM tasks WHERE id=?", (task_id,), one=True)
    if not row:
        return {"ok": False, "msg": f"任务 {task_id} 不存在"}
    if row["status"] != "review":
        return {"ok": False, "msg": f"任务当前状态 {row['status']}，仅 review 状态可评审"}
    project = row["project"] or "default"
    if approve:
        db("UPDATE tasks SET status='done', feedback=?, updated_at=? WHERE id=?",
           (feedback, time.time(), task_id))
        _refresh_blocks()
        try:
            memory_store.mark_verified(project, task_id)
        except Exception as e:
            print(f"[memory] mark_verified 失败（忽略）: {e}")
        return {"ok": True, "task_id": task_id, "status": "done"}
    db("UPDATE tasks SET status='doing', feedback=?, updated_at=? WHERE id=?",
       (f"[review by {reviewer or 'anonymous'}] {feedback}", time.time(), task_id))
    try:
        memory_store.write(project, "decision", f"打回「{row['title']}」：{feedback}",
                           tags=f"task={task_id} reviewer={reviewer or '-'}", task_id=task_id)
    except Exception as e:
        print(f"[memory] decision 写入失败（忽略）: {e}")
    return {"ok": True, "task_id": task_id, "status": "doing", "msg": "已打回返工"}


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
def spec_write(project: str, name: str, content: str, note: str = "") -> dict:
    """写项目级 Spec/设计文档（PRD、架构约定、接口契约等），供该项目下所有智能体 spec_read 对齐上下文。
    同名覆盖即更新版本。P7 起 Spec 落在项目工作舱 workspaces/<project>/spec/ 内。"""
    name = _safe_name(name)
    pdir = memory_store.spec_dir(project)
    path = pdir / name
    path.write_text(content, encoding="utf-8")
    db("INSERT INTO artifacts (task_id,name,path,note,created_at) VALUES (?,?,?,?,?)",
       (f"spec:{project or 'default'}", name, str(path), note, time.time()))
    return {"ok": True, "project": project, "name": name, "path": str(path)}


@mcp.tool()
def spec_read(project: str, name: str) -> dict:
    """读项目级 Spec 文档。认领任务后先读它再干活。"""
    name = _safe_name(name)
    path = memory_store.spec_dir(project, create=False) / name
    if not path.exists():      # 兼容 P3 旧位置（未迁移/历史记录）
        legacy = SPECS_LEGACY / memory_store.workspace_dir(project, create=False).name / name
        if legacy.exists():
            path = legacy
        else:
            return {"ok": False, "msg": f"项目 {project} 的 Spec {name} 不存在，可先 spec_list 查看"}
    return {"ok": True, "project": project, "name": name,
            "content": path.read_text(encoding="utf-8", errors="replace")}


@mcp.tool()
def spec_list(project: str = "") -> dict:
    """列出项目级 Spec 文档；project 留空列出全部项目。"""
    if project:
        rows = db("SELECT name,note,created_at FROM artifacts WHERE task_id=? ORDER BY id DESC",
                  (f"spec:{project}",))
    else:
        rows = db("SELECT task_id AS project,name,note,created_at FROM artifacts WHERE task_id LIKE 'spec:%' ORDER BY id DESC LIMIT 200")
    return {"count": len(rows), "specs": [dict(r) for r in rows]}


@mcp.tool()
def task_get(task_id: str) -> dict:
    """查看任务详情（含状态、依赖、review 反馈、结果、产物与子任务清单）。"""
    row = db("SELECT * FROM tasks WHERE id=?", (task_id,), one=True)
    if not row:
        return {"ok": False, "msg": f"任务 {task_id} 不存在"}
    return {"ok": True, "task": _task_full(row)}


# ---------------- P7 工作舱记忆工具（跨客户端/跨会话上下文不丢） ----------------
@mcp.tool()
def memory_write(project: str, layer: str, content: str, tags: str = "", key: str = "") -> dict:
    """写工作舱记忆，供该项目下所有智能体后续任务自动召回。
    layer：core（核心事实/铁律/技术栈——每次任务都注入，key 同名覆盖）/
           decision（决策："为什么选 A 不选 B"、评审结论——按 kind/关键词召回）/
           episodic（情节：交付摘要、踩过的坑——FTS5 top-k 召回）。
    换客户端/换机器/换会话，只要 hub/data/workspaces 在，记忆就在。"""
    return memory_store.write(project, layer, content, tags=tags, key=key)


@mcp.tool()
def memory_read(project: str, query: str = "", layer: str = "", limit: int = 5) -> dict:
    """检索工作舱记忆（SQLite FTS5，中文子串友好）。query 为空则按 layer 列最近记忆。"""
    rows = (memory_store.search(project, query, layer, limit) if (query or "").strip()
            else memory_store.list_layer(project, layer)[:limit])
    return {"ok": True, "project": project, "query": query, "count": len(rows), "memory": rows}


@mcp.tool()
def memory_list(project: str, layer: str = "") -> dict:
    """列出某工作舱全部记忆（layer 留空则列 core/decision/episodic 三层）。"""
    rows = memory_store.list_layer(project, layer)
    return {"ok": True, "project": project, "layer": layer or "*", "count": len(rows), "memory": rows}


@mcp.tool()
def memory_forget(project: str, id: int) -> dict:
    """删除一条记忆（记忆要可查可改，对齐 PilotDeck 主张）。"""
    return memory_store.forget(project, id)


# ---------------- REST（Hub 侧建单/查单/看板/指标摄入/观测看板） ----------------
class TaskIn(BaseModel):
    title: str
    instructions: str


async def rest_create(req: Request):
    body = await req.json()
    title, ins = (body.get("title") or "").strip(), (body.get("instructions") or "").strip()
    if not title or not ins:
        return JSONResponse(status_code=400, content={"error": "title / instructions 必填"})
    r = _create_task(title, ins,
                     project=body.get("project") or "default",
                     parent_id=body.get("parent_id") or "",
                     depends_on=body.get("depends_on"),
                     priority=body.get("priority") or 0,
                     kind=body.get("kind") or "task",
                     output_dir=body.get("output_dir") or "")
    if not r["ok"]:
        return JSONResponse(status_code=400, content={"error": r["msg"]})
    return JSONResponse({"id": r["id"], "status": r["status"]})


async def rest_list(req: Request):
    _refresh_blocks()
    rows = db("SELECT * FROM tasks ORDER BY created_at DESC LIMIT 200")
    return JSONResponse([dict(r) for r in rows])


async def rest_get(req: Request):
    tid = req.path_params["tid"]
    row = db("SELECT * FROM tasks WHERE id=?", (tid,), one=True)
    if not row:
        return JSONResponse(status_code=404, content={"error": "任务不存在"})
    return JSONResponse(_task_full(row))


async def rest_board(req: Request):
    _refresh_blocks()
    project = req.query_params.get("project", "")
    if project:
        rows = db("SELECT id,title,status,claimed_by,parent_id,kind,priority,updated_at FROM tasks WHERE project=? ORDER BY priority DESC, created_at", (project,))
    else:
        rows = db("SELECT id,title,status,claimed_by,parent_id,project,kind,priority,updated_at FROM tasks ORDER BY priority DESC, created_at")
    board = {c: [] for c in COLUMNS}
    for r in rows:
        d = dict(r)
        board.setdefault(d["status"], board["open"]).append(d)
    return JSONResponse({"project": project or "*",
                         "counts": {c: len(v) for c, v in board.items()}, "board": board})


async def healthz(req: Request):
    n = db("SELECT COUNT(*) c FROM tasks WHERE status='open'", one=True)["c"]
    return JSONResponse({"ok": True, "open_tasks": n})


async def rest_metrics_post(req: Request):
    """任意客户端自报用量（网关 / Codex CLI / 自定义脚本），body 为单对象或数组。
    字段同 metrics 表：source(必填)、channel、kind、project、task_id、ok、latency_ms、
    prompt_chars、answer_chars、est_tokens、cost_class、cost_usd、note。"""
    body = await req.json()
    items = body if isinstance(body, list) else [body]
    allowed = set(metrics_log.FIELDS)
    n = 0
    with _lock:
        for it in items:
            if not isinstance(it, dict) or not str(it.get("source") or "").strip():
                continue
            metrics_log.log(_conn, **{k: it[k] for k in it if k in allowed})
            n += 1
        _conn.commit()
    return JSONResponse({"ok": True, "ingested": n})


async def dashboard_page(req: Request):
    """用量与效果看板：统一指标表（通道/延迟/字符/限流）+ 任务漏斗 + 客户端 + 工作舱记忆。"""
    return HTMLResponse(dashboard.render(dashboard.collect()))


# ---------------- 组合应用：/mcp + /sse + /api ----------------
http_app = mcp.streamable_http_app()   # 内部路由：/mcp
sse_app = mcp.sse_app()                # 内部路由：/sse + /messages/

routes = list(http_app.routes) + list(sse_app.routes) + [
    Route("/api/tasks", rest_create, methods=["POST"]),
    Route("/api/tasks", rest_list, methods=["GET"]),
    Route("/api/tasks/{tid}", rest_get, methods=["GET"]),
    Route("/api/board", rest_board, methods=["GET"]),
    Route("/api/metrics", rest_metrics_post, methods=["POST"]),
    Route("/dashboard", dashboard_page, methods=["GET"]),
    Route("/healthz", healthz, methods=["GET"]),
]

app = Starlette(routes=routes, lifespan=http_app.router.lifespan_context)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=4300, log_level="warning")
