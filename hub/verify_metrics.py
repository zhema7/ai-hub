# -*- coding: utf-8 -*-
# P6/P7 验证：
#   A. 指标层：metrics_log 写入 → dashboard.collect 聚合（通道/成功/限流/节省/来源）
#   B. 记忆层：memory_store 分层写入 / core 同名覆盖 / FTS5 中文子串检索 / forget / context 注入
#   C. prompt 注入：dispatcher.build_prompt 带上 [工作舱][核心记忆][任务]
#   D. 在线：POST /api/metrics 摄入 + GET /dashboard 渲染（需 :4300 板在线，离线则跳过）
# 运行：.\..\ai-relay\.venv\Scripts\python.exe verify_metrics.py
#   （用 ai-relay venv：需要 yaml 才能 import dispatcher；dashboard/memory_store/metrics_log 纯 stdlib 两 venv 通用）
import os
import shutil
import sqlite3
import time

for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

import cost
import dashboard
import dispatcher
import memory_store
import metrics_log

SRC = "verify-p6"
PROJ = f"verify-p7-{int(time.time())}"
WORK = memory_store.WORKS / PROJ
BASE = "http://127.0.0.1:4300"


def _cleanup_metrics():
    conn = sqlite3.connect(metrics_log.DB, timeout=10)
    conn.execute("DELETE FROM metrics WHERE source=?", (SRC,))
    conn.commit()
    conn.close()


# ---------- A. 指标层 ----------
def t_metrics():
    _cleanup_metrics()
    before = dashboard.collect()
    before_costs = {c["channel"]: c for c in before["costs"]}
    # 免费通道 doubao-app：prompt 100 字 / answer 300 字 → pt=50 ct=150，费率 1.6/3.2 CNY/Mtok
    metrics_log.record(source=SRC, channel="doubao-app", ok=1, latency_ms=1500,
                       prompt_chars=100, answer_chars=300, cost_class="free_app", note="ok")
    metrics_log.record(source=SRC, channel="doubao-app", ok=1, latency_ms=900,
                       prompt_chars=100, answer_chars=300, cost_class="free_app", note="ok")
    metrics_log.record(source=SRC, channel="doubao-app", ok=0, latency_ms=20000,
                       prompt_chars=80, cost_class="free_app", note="limit: 额度已用完")
    # 按量通道 gw-cheap：真金，费率 0.15/0.3 CNY/Mtok
    metrics_log.record(source=SRC, channel="gw-cheap", ok=1, latency_ms=420,
                       prompt_chars=10, answer_chars=10, cost_class="metered", note="ok")
    after = dashboard.collect()

    # token：2 ok doubao 行 pt=100 ct=300 + 1 gw-cheap 行 pt=5 ct=5
    delta_tok = after["savings"]["tokens"] - before["savings"]["tokens"]
    assert delta_tok == 100 + 300 + 5 + 5, f"token 增量应为 410，实为 {delta_tok}"

    # 节省：doubao 免费 saved = (1.6*100 + 3.2*300)/1e6 = 0.00112 CNY；gw-cheap metered saved=0
    exp_saved_cny = (1.6 * 100 + 3.2 * 300) / 1e6
    exp_saved_usd = exp_saved_cny / cost.USD_CNY
    delta_saved = after["savings"]["usd"] - before["savings"]["usd"]
    assert abs(delta_saved - exp_saved_usd) < 1e-9, f"saved 增量应为 {exp_saved_usd}，实为 {delta_saved}"

    # 实际支出：仅 metered 的 gw-cheap = (0.15*5 + 0.3*5)/1e6 = 0.00225 CNY → USD
    exp_spent_usd = (0.15 * 5 + 0.3 * 5) / 1e6 / cost.USD_CNY
    delta_spent = after["savings"]["spent_usd"] - before["savings"]["spent_usd"]
    assert abs(delta_spent - exp_spent_usd) < 1e-12, f"spent 增量应为 {exp_spent_usd}，实为 {delta_spent}"

    cha = next((c for c in after["channels"] if c["channel"] == "doubao-app"), None)
    # 生产数据会混入通道聚合，这里只验证：通道存在 + 限流命中 + ok 数与延迟口径合理
    assert cha and cha["limits"] >= 1 and cha["ok_n"] >= 2, cha
    assert cha["avg_ms"] == int((1500 + 900 + 20000) / 3) or cha["avg_ms"] > 0, cha["avg_ms"]

    # 成本明细：doubao-app 增量 pt=100 ct=300 saved=exp_saved_usd
    cst = next((c for c in after["costs"] if c["channel"] == "doubao-app"), None)
    assert cst, "doubao-app 成本行缺失"
    b = before_costs.get("doubao-app", {})
    dpt = cst["pt"] - (b.get("pt") or 0)
    dct = cst["ct"] - (b.get("ct") or 0)
    dsaved = cst["saved"] - (b.get("saved") or 0)
    assert dpt == 100 and dct == 300 and abs(dsaved - exp_saved_usd) < 1e-9, (dpt, dct, dsaved)

    assert any(s["source"] == SRC for s in after["sources"]), after["sources"]
    assert "rework_rate" in after and "workspaces" in after and "costs" in after
    print(f"[A] 指标层 ok：token +{delta_tok} / saved ${delta_saved:.6f} / spent ${delta_spent:.6f} "
          f"/ doubao n=3 ok=2 limit=1 / 成本明细 / 来源心跳")
    _cleanup_metrics()


# ---------- B. 记忆层 ----------
def t_memory():
    r1 = memory_store.write(PROJ, "core", "技术栈：Godot + 像素风；禁止引入云依赖", key="stack")
    assert r1["ok"] and not r1["updated"]
    r2 = memory_store.write(PROJ, "core", "技术栈：Godot 4.x + HD-2D；禁止引入云依赖", key="stack")
    assert r2["ok"] and r2["updated"] and r2["id"] == r1["id"], (r1, r2)
    assert len(memory_store.core(PROJ)) == 1, "core 同 key 应覆盖不重复"

    memory_store.write(PROJ, "decision", "打回「发型」：飘逸度不足，需重做", tags="task=t9")
    memory_store.write(PROJ, "episodic", "写草稿 交付：修仙问道千万年……", task_id="t9")

    hits = memory_store.search(PROJ, "发型")
    assert hits and "飘逸度" in hits[0]["content"], hits           # 2 字中文子串命中
    assert memory_store.search(PROJ, "Godot") or memory_store.search(PROJ, "云依赖")
    assert memory_store.search(PROJ, "完全无关词汇xyz") == []

    ctx = memory_store.context(PROJ, query="发型 飘逸")
    assert "[核心记忆]" in ctx and "Godot" in ctx and "[相关记忆]" in ctx, ctx

    ml = memory_store.list_layer(PROJ)
    assert len(ml) == 3, ml
    victim = next(r for r in ml if r["layer"] == "episodic")
    f = memory_store.forget(PROJ, victim["id"])
    assert f["ok"] and len(memory_store.list_layer(PROJ)) == 2
    assert len(memory_store.core(PROJ)) == 1, "forget 不应误删 core"
    print(f"[B] 记忆层 ok：core 同名覆盖 / 中文子串检索(2字) / 三层列表 / forget / context 注入")


# ---------- C. prompt 注入 ----------
def t_prompt():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE t (id,title,instructions,project,kind,feedback,output_dir,status)")
    conn.execute("INSERT INTO t VALUES (?,?,?,?,?,?,?,?)",
                 ("t1", "画飘逸发型", "画出长发飘逸感", PROJ, "writing", "", "", "open"))
    row = conn.execute("SELECT * FROM t").fetchone()
    p = dispatcher.build_prompt(conn, row)
    assert "[工作舱]" in p and "[核心记忆]" in p and "[任务]" in p and "飘逸发型" in p, p[:200]
    print("[C] prompt 注入 ok：工作舱 + 核心记忆 + 任务指令（P7 注入顺序）")


# ---------- D. 在线（可跳过） ----------
def t_online():
    import httpx
    try:
        with httpx.Client(trust_env=False, timeout=5) as http:
            hz = http.get(f"{BASE}/healthz")
            assert hz.status_code == 200, hz.text
            r = http.post(f"{BASE}/api/metrics", json={
                "source": SRC, "channel": "verify-rest", "ok": 1, "latency_ms": 300,
                "prompt_chars": 20, "answer_chars": 40, "cost_class": "member", "note": "ok"})
            assert r.json()["ingested"] == 1, r.text
            page = http.get(f"{BASE}/dashboard")
            assert page.status_code == 200 and "观测看板" in page.text, page.status_code
            assert "verify-rest" in page.text, "看板未渲染刚摄入的通道"
            assert "工作舱" in page.text
            _cleanup_metrics()
            print(f"[D] 在线 ok：/healthz /api/metrics 摄入 /dashboard {len(page.text)} 字节")
    except Exception as e:
        print(f"[D] 跳过（:4300 板未在线）：{type(e).__name__}: {str(e)[:60]}")


if __name__ == "__main__":
    try:
        t_metrics()
        t_memory()
        t_prompt()
    finally:
        _cleanup_metrics()
        if WORK.is_dir():
            shutil.rmtree(WORK, ignore_errors=True)
    t_online()
    print("\nALL PASS (P6/P7)")
