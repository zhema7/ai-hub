# -*- coding: utf-8 -*-
# P4 能力路由验证：
#   A. 离线单测（注入假健康状态）：kind 匹配 / 成本升序 / rank 升序 / 全灭返回空链
#   B. 端到端：REST 建 writing 任务 → dispatcher --once 路由到 doubao-app 执行 →
#      账本落 review + 产物 → MCP task_review 通过（P3+P4 闭环）
#   C. 无可用通道的任务（realtime，通道全规划）→ failed
# 运行：.\..\ai-relay\.venv\Scripts\python.exe verify_router.py（需 :4300 板 + :4301 豆包桥在线）
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

# 7993 坑：MCP/httpx 客户端默认 trust_env=True，先清代理
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

import httpx
import router

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "board.db"
BASE = "http://127.0.0.1:4300"
PROJ = f"verify-p4-{int(time.time())}"


# ---------- A. 离线单测 ----------
def t_offline():
    cfg = router.load_config()

    def alive_only(*ids):
        return lambda ch: bool(ch.get("enabled")) and ch["id"] in ids

    # 全部启用通道在线：成本优先 free_app > member
    chain = router.route("chat", cfg, alive_fn=alive_only("doubao-app", "kimi-vip"))
    assert [c["id"] for c in chain] == ["doubao-app", "kimi-vip"], chain
    # kind 匹配：writing → doubao-app；coding → kimi-vip
    assert router.route("writing", cfg, alive_fn=alive_only("doubao-app", "kimi-vip"))[0]["id"] == "doubao-app"
    assert router.route("coding", cfg, alive_fn=alive_only("doubao-app", "kimi-vip"))[0]["id"] == "kimi-vip"
    # 首选挂了自动退次选
    chain = router.route("chat", cfg, alive_fn=alive_only("kimi-vip"))
    assert [c["id"] for c in chain] == ["kimi-vip"]
    # 未指定 kind → chat 路由
    assert router.route("", cfg, alive_fn=alive_only("doubao-app"))[0]["id"] == "doubao-app"
    # 全灭/全未启用 → 空链
    assert router.route("realtime", cfg, alive_fn=alive_only("doubao-app", "kimi-vip")) == []
    assert router.route("chat", cfg, alive_fn=alive_only()) == []
    print("[A] 离线路由单测 ok：kind 匹配 / 成本升序(free_app>member) / 故障退次选 / 空链兜底")


# ---------- B. 端到端 ----------
def t_e2e():
    # B1) REST 建 writing 任务
    with httpx.Client(trust_env=False, timeout=10) as http:
        tid = http.post(f"{BASE}/api/tasks", json={
            "title": "验证任务：一句口号",
            "instructions": "为'地下城与修仙'写一句宣传口号，不超过15个字，只回答口号本身。",
            "project": PROJ, "kind": "writing",
        }).json()["id"]
        print(f"[B1] REST 建单 ok: {tid} (kind=writing)")

    # B2) dispatcher 单轮调度（应路由 doubao-app，GUI 单轮 25-40s）
    py = sys.executable
    r = subprocess.run([py, "dispatcher.py", "--once", "--project", PROJ],
                       cwd=ROOT, capture_output=True, text=True, timeout=240, encoding="utf-8", errors="replace")
    print("[B2] dispatcher:", (r.stdout or r.stderr).strip().replace("\n", " | ")[:200])
    assert r.returncode == 0, r.stderr

    # B3) 账本应为 review + result.md 产物
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    t = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    assert t["status"] == "review" and t["claimed_by"] == "dispatcher-p4", dict(t)
    art = conn.execute("SELECT path FROM artifacts WHERE task_id=? AND name='result.md'", (tid,)).fetchone()
    answer = Path(art["path"]).read_text(encoding="utf-8")
    assert answer.strip(), "空回答"
    print(f"[B3] 路由执行 ok：{t['result']} | 回答={answer.strip()[:30]}")

    # B4) MCP task_review 通过（P3+P4 闭环）
    import asyncio
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def approve():
        async with streamablehttp_client(f"{BASE}/mcp") as (rd, wr, _):
            async with ClientSession(rd, wr) as s:
                await s.initialize()
                res = json.loads((await s.call_tool("task_review", {
                    "task_id": tid, "approve": True, "reviewer": "verify-p4"})).content[0].text)
                assert res["status"] == "done", res
    asyncio.run(approve())
    print("[B4] task_review 通过 → done（P3 review 流 × P4 自动调度闭环）")


# ---------- C. 无可用通道 ----------
def t_no_channel():
    with httpx.Client(trust_env=False, timeout=10) as http:
        tid = http.post(f"{BASE}/api/tasks", json={
            "title": "验证任务：实时热点", "instructions": "x",
            "project": PROJ, "kind": "realtime",
        }).json()["id"]
    r = subprocess.run([sys.executable, "dispatcher.py", "--once", "--project", PROJ],
                       cwd=ROOT, capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    t = conn.execute("SELECT status, error FROM tasks WHERE id=?", (tid,)).fetchone()
    assert t["status"] == "failed" and "无可用通道" in t["error"], dict(t)
    print("[C] 无可用通道 → failed ok:", t["error"])


if __name__ == "__main__":
    t_offline()
    t_e2e()
    t_no_channel()
    print("\nALL PASS (P4)")
