# -*- coding: utf-8 -*-
# MCP Facade 全流程验证：REST 建单 → MCP 看板/认领/产物/回写 → REST 查单
# 运行：先 start-mcp.ps1，再  .\..\ai-relay\.venv\Scripts\python.exe verify_mcp.py
import asyncio
import json

import httpx

BASE = "http://127.0.0.1:4300"


async def main():
    # 1) Hub 侧 REST 建单
    async with httpx.AsyncClient(trust_env=False, timeout=10) as http:
        r = await http.post(f"{BASE}/api/tasks", json={
            "title": "验证任务：写一首两句的打油诗",
            "instructions": "写一首关于'地下城与修仙'的两句打油诗，用 artifact_write 提交，再 task_report done。",
        })
        tid = r.json()["id"]
        print(f"[1] REST 建单 ok: {tid}")

    # 2) MCP 客户端全流程（模拟平台软件连入）
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(f"{BASE}/mcp") as (read, write, _):
        async with ClientSession(read, write) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("[2] MCP tools:", sorted(t.name for t in tools.tools))

            lst = (await s.call_tool("task_list", {"status": "open"})).content[0].text
            print("[3] task_list:", lst[:120], "...")

            claimed = json.loads((await s.call_tool("task_claim", {"task_id": tid, "client_name": "verify-client"})).content[0].text)
            assert claimed["ok"], claimed
            print("[4] task_claim ok, instructions:", claimed["task"]["instructions"][:30], "...")

            art = json.loads((await s.call_tool("artifact_write", {
                "task_id": tid, "name": "poem.txt",
                "content": "修仙问道千万年，地下城中觅机缘。"})).content[0].text)
            print("[5] artifact_write ok:", art["path"], art["bytes"], "bytes")

            read = json.loads((await s.call_tool("artifact_read", {"task_id": tid, "name": "poem.txt"})).content[0].text)
            print("[6] artifact_read ok:", read["content"])

            rep = json.loads((await s.call_tool("task_report", {
                "task_id": tid, "status": "done", "result_summary": "打油诗已写入 artifact poem.txt"})).content[0].text)
            assert rep["ok"]
            print("[7] task_report done ok")

    # 3) Hub 侧查单（应见 done + 产物）
    async with httpx.AsyncClient(trust_env=False, timeout=10) as http:
        t = (await http.get(f"{BASE}/api/tasks/{tid}")).json()
        print(f"[8] REST 查单: status={t['status']}, result={t['result']}, artifacts={[a['name'] for a in t['artifacts']]}")

    # 4) 重复认领应被拒绝（测试第二单）
    async with httpx.AsyncClient(trust_env=False, timeout=10) as http:
        tid2 = (await http.post(f"{BASE}/api/tasks", json={"title": "竞态单", "instructions": "x"})).json()["id"]
    from mcp.client.sse import sse_client
    async with sse_client(f"{BASE}/sse") as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            await s.call_tool("task_claim", {"task_id": tid2, "client_name": "sse-a"})
            dup = json.loads((await s.call_tool("task_claim", {"task_id": tid2, "client_name": "sse-b"})).content[0].text)
            print("[9] SSE 通道 ok; 重复认领拒绝:", not dup["ok"], "-", dup["msg"])

    print("\nALL PASS")


asyncio.run(main())
