# -*- coding: utf-8 -*-
# AI Hub · P4 调度守护（能力路由执行器）
# 轮询看板 open 任务 → 原子认领 → router 按 kind 选通道（成本×能力 fallback 链）→
# 经 OpenAI 兼容端点执行 → 产物落 artifacts + 账本回写
#   成功 → review（走 P3 review 流，由人/智能体 task_review 裁决）
#   全链失败 → failed（error 记录每条通道的失败原因）
# 直接读写 board.db（账本即真相，与 MCP 板进程解耦；SQLite 短写 + busy 重试）
#
# 用法：
#   python dispatcher.py --once            跑一轮（验证用）
#   python dispatcher.py --interval 5      常驻轮询
#   python dispatcher.py --dry-run         只打印路由决策不执行
import argparse
import json
import os
import sqlite3
import time
from pathlib import Path

import httpx
import memory_store
import metrics_log
import router

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "board.db"
ARTS = DATA / "artifacts"
ENV_FILE = ROOT.parent / "ai-relay" / ".env"

CLIENT = "dispatcher-p4"


def _load_env() -> dict:
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


ENV = _load_env()


def _db():
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def claim_next(conn, project: str = "") -> sqlite3.Row | None:
    """优先级高者优先，同级先进先出；原子认领防 MCP 侧/多实例竞争。project 非空时只领该项目。"""
    sql = "SELECT * FROM tasks WHERE status='open'"
    args: list = []
    if project:
        sql += " AND project=?"
        args.append(project)
    rows = conn.execute(sql + " ORDER BY priority DESC, created_at ASC LIMIT 5", args).fetchall()
    for r in rows:
        cur = conn.execute(
            "UPDATE tasks SET status='doing', claimed_by=?, updated_at=? WHERE id=? AND status='open'",
            (CLIENT, time.time(), r["id"]))
        conn.commit()
        if cur.rowcount:
            return conn.execute("SELECT * FROM tasks WHERE id=?", (r["id"],)).fetchone()
    return None


def build_prompt(task: sqlite3.Row) -> str:
    """P7 注入顺序：工作舱 → 核心记忆（常驻）→ 相关记忆（FTS5 top-k）→
    项目 Spec → 任务 → 上轮 review 反馈。"""
    project = task["project"] or "default"
    parts = [f"[工作舱] 项目 {project}"]
    try:
        mem = memory_store.context(project, query=f"{task['title']} {task['kind'] or ''}", k=5)
    except Exception as e:
        mem = ""
        print(f"[memory] 注入失败（忽略）: {type(e).__name__}: {e}")
    if mem:
        parts.append(mem)
    sdir = memory_store.spec_dir(project, create=False)
    specs = sorted(sdir.glob("*.md")) if sdir.is_dir() else []
    if specs:
        parts.append("[项目 Spec 上下文]")
        for sp in specs:
            parts.append(f"--- {sp.name} ---\n{sp.read_text(encoding='utf-8', errors='replace')}")
        parts.append("[Spec 结束，请严格遵循]")
    parts.append(f"[任务] {task['title']}\n{task['instructions']}")
    if task["feedback"]:
        parts.append(f"[上一轮 review 反馈，请针对性修改] {task['feedback']}")
    return "\n\n".join(parts)


def execute(prompt: str, chain: list[dict]) -> tuple[str, dict]:
    """按 fallback 链逐通道尝试，返回 (回答, 命中的通道)；全失败抛异常。"""
    errors = []
    for ch in chain:
        headers = {}
        key_env = ch.get("api_key_env")
        if key_env and ENV.get(key_env):
            headers["Authorization"] = f"Bearer {ENV[key_env]}"
        try:
            with httpx.Client(trust_env=False, timeout=float(ch.get("timeout", 120))) as http:
                r = http.post(f"{ch['endpoint']}/chat/completions", headers=headers, json={
                    "model": ch["model"],
                    "messages": [{"role": "user", "content": prompt}],
                })
                r.raise_for_status()
                data = r.json()
                answer = (data["choices"][0]["message"]["content"] or "").strip()
                if not answer:
                    raise ValueError("空回答")
                return answer, ch
        except Exception as e:
            errors.append(f"{ch['id']}: {type(e).__name__} {str(e)[:120]}")
    raise RuntimeError("全链路失败 | " + " ; ".join(errors))


def finish(conn, task: sqlite3.Row, ok: bool, answer: str = "", ch: dict | None = None,
           error: str = "", prompt_chars: int = 0, latency_ms: int = 0):
    """交付/失败回写 + 记账：产物落盘、episodic 记忆、metrics 埋点（P6a/P7b）。"""
    task_id, project = task["id"], task["project"] or "default"
    kind, output_dir = task["kind"] or "", task["output_dir"] or ""
    channel, cost_class = (ch["id"] if ch else ""), (ch.get("cost", "") if ch else "")
    if ok:
        tdir = ARTS / task_id
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / "result.md").write_text(answer, encoding="utf-8")
        # 产物直落：output_dir 非空时把结果复制进指定目录（如 Trae 项目文件夹）
        delivered = str(tdir / "result.md")
        if output_dir:
            try:
                odir = Path(output_dir)
                odir.mkdir(parents=True, exist_ok=True)
                dest = odir / f"aihub-{task_id}.md"
                dest.write_text(answer, encoding="utf-8")
                delivered = str(dest)
            except Exception as e:
                print(f"[output_dir] 复制失败 {task_id}: {e}")
        conn.execute("INSERT INTO artifacts (task_id,name,path,note,created_at) VALUES (?,?,?,?,?)",
                     (task_id, "result.md", str(tdir / "result.md"),
                      f"auto-dispatch via {channel} ({ch['model']})" +
                      (f" → 落 {delivered}" if output_dir else ""), time.time()))
        conn.execute("UPDATE tasks SET status='review', result=?, updated_at=? WHERE id=?",
                     (f"[{CLIENT}] 通道 {channel} (cost={cost_class}) 已交付，待 review" +
                      (f"；产物在 {delivered}" if output_dir and delivered != str(tdir / 'result.md') else ""),
                      time.time(), task_id))
        # P7：交付成功自动写情节记忆（不靠人手动记）
        try:
            memory_store.write(project, "episodic",
                               f"{task['title']} 交付：{answer[:180].replace(chr(10), ' ')}（产物 {delivered}）",
                               tags=f"kind={kind} channel={channel}", task_id=task_id)
        except Exception as e:
            print(f"[memory] episodic 写入失败（忽略）: {type(e).__name__}: {e}")
        metrics_log.log(conn, source="board", channel=channel, kind=kind, project=project,
                        task_id=task_id, ok=1, latency_ms=latency_ms, prompt_chars=prompt_chars,
                        answer_chars=len(answer), cost_class=cost_class, cost_usd=0.0,
                        note=f"delivered via {channel}")
    else:
        conn.execute("UPDATE tasks SET status='failed', error=?, updated_at=? WHERE id=?",
                     (error[:500], time.time(), task_id))
        metrics_log.log(conn, source="board", channel=channel, kind=kind, project=project,
                        task_id=task_id, ok=0, latency_ms=latency_ms, prompt_chars=prompt_chars,
                        answer_chars=0, cost_class=cost_class, note=f"failed: {error[:120]}")
    conn.commit()


def run_once(dry_run: bool = False, project: str = "") -> int:
    """处理一轮 open 任务，返回处理数。"""
    cfg = router.load_config()
    n = 0
    with _db() as conn:
        while True:
            task = claim_next(conn, project)
            if not task:
                break
            n += 1
            chain = router.route(task["kind"], cfg)
            print(f"[dispatch] {task['id']} kind={task['kind']} "
                  f"chain={' → '.join(c['id'] for c in chain) or '无可用通道'}")
            if not chain:
                finish(conn, task, False, error="无可用通道（全部未启用或离线）")
                continue
            prompt = build_prompt(task)
            t0 = time.time()
            if dry_run:
                finish(conn, task, False, error="dry-run：仅验证路由决策", prompt_chars=len(prompt))
                continue
            try:
                answer, ch = execute(prompt, chain)
                finish(conn, task, True, answer, ch, prompt_chars=len(prompt),
                       latency_ms=int((time.time() - t0) * 1000))
                print(f"[done→review] {task['id']} via {ch['id']} ({len(answer)} chars)")
            except Exception as e:
                finish(conn, task, False, error=str(e), prompt_chars=len(prompt),
                       latency_ms=int((time.time() - t0) * 1000))
                print(f"[failed] {task['id']} {e}")
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="跑一轮后退出")
    ap.add_argument("--interval", type=float, default=5.0, help="轮询间隔秒")
    ap.add_argument("--dry-run", action="store_true", help="只打印路由决策")
    ap.add_argument("--project", default="", help="只处理指定 project 的任务")
    args = ap.parse_args()

    if args.once:
        run_once(args.dry_run, args.project)
        return
    print(f"dispatcher 常驻轮询 board.db（每 {args.interval}s，project={args.project or '*'}），Ctrl+C 退出")
    while True:
        try:
            run_once(args.dry_run, args.project)
        except Exception as e:
            print(f"[loop error] {type(e).__name__}: {e}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
