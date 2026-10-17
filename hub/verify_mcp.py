# -*- coding: utf-8 -*-
# MCP Facade P3 看板全流程验证：
#   REST 建根任务 → MCP 拆子任务+依赖阻塞 → spec 共享 → 认领 → review 打回返工 →
#   通过解锁下游 → task_board 看板 → 旧接口兼容 → SSE 通道竞态拒绝
# 运行：先 start-mcp.ps1，再  .\..\ai-relay\.venv\Scripts\python.exe verify_mcp.py
import asyncio
import json
import os
import time

# 7993 坑：MCP 客户端内部 httpx 默认 trust_env=True，会被系统代理截胡——先清干净
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

import httpx

BASE = "http://127.0.0.1:4300"
PROJ = f"verify-p3-{int(time.time())}"   # 每轮唯一 project，避免历史残留撞断言


async def main():
    # 1) Hub 侧 REST 建根任务（带 project）
    async with httpx.AsyncClient(trust_env=False, timeout=10) as http:
        r = await http.post(f"{BASE}/api/tasks", json={
            "title": "验证项目：地下城与修仙宣传文案",
            "instructions": "产出一篇两句宣传文案。拆两个子任务：草稿 → 终稿（终稿依赖草稿 + spec）。",
            "project": PROJ,
        })
        root = r.json()
        root_id = root["id"]
        assert root["status"] == "open", root
        print(f"[1] REST 建根任务 ok: {root_id} (project={PROJ})")

    # 2) MCP 客户端全流程
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(f"{BASE}/mcp") as (read, write, _):
        async with ClientSession(read, write) as s:
            await s.initialize()
            tools = sorted(t.name for t in (await s.list_tools()).tools)
            print("[2] MCP tools:", tools)
            for t in ("task_create", "task_board", "task_review", "task_subtasks", "spec_write", "spec_read"):
                assert t in tools, f"缺少 P3 工具 {t}"

            async def call(name, args):
                return json.loads((await s.call_tool(name, args)).content[0].text)

            # 3) spec 共享：写项目文档 → 读回
            sp = await call("spec_write", {"project": PROJ, "name": "brief.md",
                                           "content": "调性：热血+机缘；禁用词：最强、无敌；两句以内。"})
            assert sp["ok"], sp
            sp2 = await call("spec_read", {"project": PROJ, "name": "brief.md"})
            assert "禁用词" in sp2["content"]
            sl = await call("spec_list", {"project": PROJ})
            print(f"[3] spec_write/spec_read/spec_list ok ({sl['count']} 篇): brief.md = {sp2['content'][:16]}...")

            # 4) 拆子任务：draft 无依赖 → open；final 依赖 draft → blocked
            draft = await call("task_create", {"title": "写草稿", "instructions": "按 brief.md 写两句草稿。",
                                               "project": PROJ, "parent_id": root_id, "kind": "writing"})
            assert draft["status"] == "open", draft
            final = await call("task_create", {"title": "出终稿", "instructions": "草稿 review 通过后润色为终稿。",
                                               "project": PROJ, "parent_id": root_id, "depends_on": draft["id"]})
            assert final["status"] == "blocked", final
            subs = await call("task_subtasks", {"parent_id": root_id})
            assert subs["count"] == 2
            print(f"[4] 子任务拆分 ok: draft={draft['id']}(open), final={final['id']}(blocked 依赖 draft)")

            # 5) blocked 不可认领
            blocked_claim = await call("task_claim", {"task_id": final["id"], "client_name": "verify-client"})
            assert not blocked_claim["ok"] and "阻塞" in blocked_claim["msg"]
            print("[5] blocked 认领被拒 ok:", blocked_claim["msg"])

            # 6) 认领 draft → 交付 review → 打回 → 再交付 → 通过
            c = await call("task_claim", {"task_id": draft["id"], "client_name": "verify-client"})
            assert c["ok"] and c["task"]["status"] == "doing"
            await call("artifact_write", {"task_id": draft["id"], "name": "draft.txt",
                                          "content": "修仙问道千万年，地下城中觅机缘。"})
            r1 = await call("task_report", {"task_id": draft["id"], "status": "review",
                                            "result_summary": "草稿已落 artifact draft.txt"})
            assert r1["ok"] and r1["status"] == "review"
            rej = await call("task_review", {"task_id": draft["id"], "approve": False,
                                             "feedback": "第二句太平，加'热血'感", "reviewer": "verify-lead"})
            assert rej["ok"] and rej["status"] == "doing"
            g = await call("task_get", {"task_id": draft["id"]})
            assert "热血" in g["task"]["feedback"]
            print("[6] 认领→review→打回 ok，feedback 回流:", g["task"]["feedback"][:24], "...")

            r2 = await call("task_report", {"task_id": draft["id"], "status": "review",
                                            "result_summary": "已按反馈加强语气"})
            ok = await call("task_review", {"task_id": draft["id"], "approve": True, "reviewer": "verify-lead"})
            assert ok["status"] == "done"
            print("[7] 返工后 review 通过 → done")

            # 8) 依赖自动解锁：final 应已从 blocked → open
            f = await call("task_get", {"task_id": final["id"]})
            assert f["task"]["status"] == "open", f["task"]["status"]
            c2 = await call("task_claim", {"task_id": final["id"], "client_name": "verify-client"})
            assert c2["ok"]
            await call("task_report", {"task_id": final["id"], "status": "done", "result_summary": "终稿完成"})
            print(f"[8] 依赖解锁 ok: final {final['id']} blocked→open→doing→done")

            # 9) task_board 看板列
            b = await call("task_board", {"project": PROJ})
            assert b["counts"]["done"] == 2 and b["counts"]["open"] == 1, b["counts"]
            print("[9] task_board ok:", b["counts"])

            # 10) 旧接口兼容：task_list(open) 应见根任务
            lst = await call("task_list", {"status": "open"})
            assert any(t["id"] == root_id for t in lst["tasks"])
            print("[10] 旧 task_list 兼容 ok")

    # 11) REST 查根任务（应带子任务清单）
    async with httpx.AsyncClient(trust_env=False, timeout=10) as http:
        t = (await http.get(f"{BASE}/api/tasks/{root_id}")).json()
        assert len(t["subtasks"]) == 2 and t["project"] == PROJ
        bd = (await http.get(f"{BASE}/api/board", params={"project": PROJ})).json()
        assert bd["counts"]["done"] == 2
        print(f"[11] REST 查单 ok: 子任务={[s['status'] for s in t['subtasks']]}, /api/board counts={bd['counts']}")

    # 12) SSE 通道 + 重复认领拒绝
    async with httpx.AsyncClient(trust_env=False, timeout=10) as http:
        tid2 = (await http.post(f"{BASE}/api/tasks", json={"title": "竞态单", "instructions": "x"})).json()["id"]
    from mcp.client.sse import sse_client
    async with sse_client(f"{BASE}/sse") as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            await s.call_tool("task_claim", {"task_id": tid2, "client_name": "sse-a"})
            dup = json.loads((await s.call_tool("task_claim", {"task_id": tid2, "client_name": "sse-b"})).content[0].text)
            assert not dup["ok"]
            print("[12] SSE 通道 ok; 重复认领拒绝:", dup["msg"][:30], "...")

    print("\nALL PASS (P3)")


asyncio.run(main())
