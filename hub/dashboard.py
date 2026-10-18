# -*- coding: utf-8 -*-
# AI Hub · P6 观测看板（聚合 + 零依赖 HTML 渲染）
# collect() 只读 board.db(metrics/tasks/artifacts) + 各工作舱 memory.db，产出结构化数据；
# render() 把数据拼成自包含 HTML（内联 CSS，30s 自刷新，无前端构建/无外网依赖）。
# mcp_server 挂在 :4300 /dashboard；verify_metrics 直接调 collect() 做断言。
import html
import sqlite3
import time
from pathlib import Path

import memory_store

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "board.db"
WORKS = ROOT / "data" / "workspaces"

WINDOW = 24 * 3600           # 看板时间窗：近 24 小时
RATE_USD_PER_MTOK = 1.0      # 等效按量价（$ / 百万 token），用于"省下多少"的粗估，看板标注 est.


def _conn():
    conn = sqlite3.connect(DB, timeout=10)
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    return conn


def _q(conn, sql, args=()) -> list:
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    except sqlite3.OperationalError:      # metrics 表尚未建立
        return []


def collect(db=DB, now: float | None = None) -> dict:
    """一次性聚合。db 可注入（单测用临时库）。"""
    now = now if now is not None else time.time()
    since = now - WINDOW
    conn = sqlite3.connect(str(db), timeout=10)
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    try:
        d: dict = {"now": now, "window_hours": WINDOW // 3600}

        # 1) 今日节省：免费/会员通道完成的用量 × 等效按量价
        r = _q(conn, "SELECT COALESCE(SUM(est_tokens),0) tok, COUNT(*) n FROM metrics "
                     "WHERE ts>=? AND ok=1 AND cost_class IN ('free_app','member')", (since,))
        tok, n = (r[0]["tok"], r[0]["n"]) if r else (0, 0)
        d["savings"] = {"tokens": tok, "calls": n, "usd": tok / 1e6 * RATE_USD_PER_MTOK,
                        "rate": RATE_USD_PER_MTOK}

        # 2) 通道热力：调用量 / 成功率 / 平均延迟 / 限流命中
        chans = _q(conn, """SELECT channel, cost_class, COUNT(*) n, SUM(ok) ok_n,
                                   AVG(latency_ms) avg_ms, SUM(est_tokens) tok,
                                   SUM(CASE WHEN note LIKE 'limit%' THEN 1 ELSE 0 END) limits
                            FROM metrics WHERE ts>=?
                            GROUP BY channel, cost_class ORDER BY n DESC""", (since,))
        for c in chans:
            c["ok_rate"] = (c["ok_n"] or 0) / c["n"] * 100 if c["n"] else 0
            c["avg_ms"] = int(c["avg_ms"] or 0)
            c["channel"] = c["channel"] or "—"
        d["channels"] = chans

        # 3) 数据源心跳（gateway / bridge / board / 任意上报方）
        d["sources"] = _q(conn, "SELECT source, COUNT(*) n, MAX(ts) last FROM metrics GROUP BY source ORDER BY last DESC")

        # 4) 任务漏斗 + 返工率（review 打回标记在 feedback 前缀）
        d["funnel"] = {r["status"]: r["n"] for r in _q(conn, "SELECT status, COUNT(*) n FROM tasks GROUP BY status")}
        rk = _q(conn, "SELECT COUNT(*) n FROM tasks WHERE feedback LIKE '[review by %'")
        d["rework"] = rk[0]["n"] if rk else 0
        done = d["funnel"].get("done", 0)
        d["rework_rate"] = (d["rework"] / (done + d["rework"]) * 100) if (done + d["rework"]) else 0

        # 5) 客户端用量（谁在干活：claimed_by 聚合）
        d["clients"] = _q(conn, """SELECT COALESCE(NULLIF(claimed_by,''),'—') client, COUNT(*) n,
                                          SUM(status='done') done, SUM(status='failed') failed,
                                          SUM(status='doing') doing, SUM(status='review') review
                                   FROM tasks GROUP BY client ORDER BY n DESC""")

        # 6) 工作舱（记忆分层 + Spec 篇数 + 产物数）
        projs = {r["project"] for r in _q(conn, "SELECT DISTINCT project FROM tasks") if r["project"]}
        if WORKS.is_dir():
            projs |= {p.name for p in WORKS.iterdir() if p.is_dir()}
        arts = {r["project"]: r["n"] for r in _q(
            conn, "SELECT t.project, COUNT(*) n FROM artifacts a JOIN tasks t ON t.id=a.task_id GROUP BY t.project")}
        ws = []
        for p in sorted(projs):
            try:
                counts = {l: len(memory_store.list_layer(p, l)) for l in memory_store.LAYERS}
                specs = len(list(memory_store.spec_dir(p, create=False).glob("*.md")))
            except Exception:
                counts, specs = {l: 0 for l in memory_store.LAYERS}, 0
            ws.append({"project": p, "core": counts["core"], "decision": counts["decision"],
                       "episodic": counts["episodic"], "specs": specs, "artifacts": arts.get(p, 0)})
        d["workspaces"] = ws
        return d
    finally:
        conn.close()


# ---------------- HTML 渲染 ----------------
_CSS = """
:root{--bg:#0f1115;--card:#171a21;--fg:#e6e9ef;--mut:#8b93a3;--line:#262b36;
--free:#39d98a;--mem:#4aa8ff;--met:#ffb454;--bad:#ff6b6b;--acc:#8b7cff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.55 -apple-system,"Segoe UI",Roboto,"Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:22px 18px 60px}
h1{font-size:19px;margin:0 0 2px} .sub{color:var(--mut);font-size:12px;margin-bottom:18px}
.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(250px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:15px 16px}
.card h2{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--mut);margin:0 0 11px}
.hero{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
.hero .big{font-size:38px;font-weight:700;color:var(--free);line-height:1}
.hero .u{color:var(--mut);font-size:12px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--mut);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:.05em}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.bar{background:#20242e;border-radius:4px;height:7px;overflow:hidden;min-width:60px}
.bar>span{display:block;height:100%;border-radius:4px}
.pill{display:inline-block;padding:1px 7px;border-radius:999px;font-size:11px;border:1px solid var(--line)}
.free{color:var(--free)}.member{color:var(--mem)}.metered{color:var(--met)}.bad{color:var(--bad)}
.muted{color:var(--mut)}.empty{color:var(--mut);font-size:12px;padding:6px 0}
.two{display:grid;gap:14px;grid-template-columns:1fr 1fr;margin-top:14px}
@media(max-width:760px){.two{grid-template-columns:1fr}}
.foot{margin-top:22px;color:var(--mut);font-size:11px}
a{color:var(--acc)}
"""


def _bar(v, mx, color):
    pct = (v / mx * 100) if mx else 0
    return f'<div class="bar"><span style="width:{pct:.1f}%;background:{color}"></span></div>'


def _cost_color(cc):
    return {"free_app": "var(--free)", "member": "var(--mem)", "metered": "var(--met)"}.get(cc, "var(--mut)")


def _ago(ts, now):
    if not ts:
        return "—"
    s = max(0, int(now - ts))
    if s < 60:
        return f"{s}s 前"
    if s < 3600:
        return f"{s // 60}m 前"
    return f"{s // 3600}h 前"


def render(d: dict) -> str:
    e = html.escape
    now = d["now"]
    sv = d["savings"]
    cnt = d["funnel"]

    # hero
    hero = (f'<div class="card"><h2>近 {d["window_hours"]}h 省下的按量成本 <span class="pill">est.</span></h2>'
            f'<div class="hero"><div class="big">${sv["usd"]:.2f}</div>'
            f'<div class="u">免费/会员通道完成 {sv["calls"]} 次 · 等效 {sv["tokens"]:,} token'
            f'（按 ${sv["rate"]:.2f}/百万 token 估算，App 通道无真 token 概念）</div></div></div>')

    # funnel
    funnel = " · ".join(f'{k} <b>{v}</b>' for k, v in
                        [("blocked", cnt.get("blocked", 0)), ("open", cnt.get("open", 0)),
                         ("doing", cnt.get("doing", 0)), ("review", cnt.get("review", 0)),
                         ("done", cnt.get("done", 0)), ("failed", cnt.get("failed", 0))])
    funnel_card = (f'<div class="card"><h2>任务漏斗</h2><div style="font-size:15px">{funnel}</div>'
                   f'<div class="muted" style="margin-top:8px;font-size:12px">返工率 '
                   f'<b class="{"bad" if d["rework_rate"] > 20 else ""}">{d["rework_rate"]:.1f}%</b>'
                   f'（review 打回 {d["rework"]} 单 / 完成 {cnt.get("done", 0)} 单）</div></div>')

    # channels
    mx = max([c["n"] for c in d["channels"]] or [1])
    if d["channels"]:
        rows = []
        for c in d["channels"]:
            rows.append(
                f'<tr><td>{_bar(c["n"], mx, _cost_color(c["cost_class"]))}</td>'
                f'<td>{e(c["channel"])} <span class="pill {e(c["cost_class"] or "muted")}">{e(c["cost_class"] or "-")}</span></td>'
                f'<td class="num">{c["n"]}</td><td class="num">{c["ok_rate"]:.0f}%</td>'
                f'<td class="num">{c["avg_ms"]:,} ms</td>'
                f'<td class="num {"bad" if c["limits"] else "muted"}">{c["limits"]}</td></tr>')
        chan_html = ('<table><tr><th></th><th>通道</th><th class="num">调用</th>'
                     '<th class="num">成功</th><th class="num">均延迟</th><th class="num">限流</th></tr>'
                     + "".join(rows) + "</table>")
    else:
        chan_html = '<div class="empty">近 24h 无通道调用记录（起 dispatcher / 桥后回填）</div>'

    # sources
    if d["sources"]:
        src_html = '<table><tr><th>来源</th><th class="num">事件</th><th class="num">最近</th></tr>' + "".join(
            f'<tr><td>{e(s["source"] or "—")}</td><td class="num">{s["n"]}</td>'
            f'<td class="num muted">{_ago(s["last"], now)}</td></tr>' for s in d["sources"]) + "</table>"
    else:
        src_html = '<div class="empty">无遥测</div>'

    # clients
    if d["clients"]:
        cli_html = ('<table><tr><th>客户端</th><th class="num">任务</th><th class="num">done</th>'
                    '<th class="num">doing</th><th class="num">failed</th></tr>' + "".join(
            f'<tr><td>{e(c["client"])}</td><td class="num">{c["n"]}</td>'
            f'<td class="num free">{c["done"] or 0}</td><td class="num">{c["doing"] or 0}</td>'
            f'<td class="num {"bad" if c["failed"] else "muted"}">{c["failed"] or 0}</td></tr>'
            for c in d["clients"]) + "</table>")
    else:
        cli_html = '<div class="empty">暂无客户端任务记录</div>'

    # workspaces
    if d["workspaces"]:
        ws_html = ('<table><tr><th>工作舱</th><th class="num">Spec</th><th class="num">core</th>'
                   '<th class="num">decision</th><th class="num">episodic</th><th class="num">产物</th></tr>'
                   + "".join(
            f'<tr><td>{e(w["project"])}</td><td class="num">{w["specs"]}</td>'
            f'<td class="num">{w["core"]}</td><td class="num">{w["decision"]}</td>'
            f'<td class="num">{w["episodic"]}</td><td class="num">{w["artifacts"]}</td></tr>'
            for w in d["workspaces"]) + "</table>")
    else:
        ws_html = '<div class="empty">暂无工作舱</div>'

    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="30">
<title>AI Hub · 观测看板</title><style>{_CSS}</style></head><body><div class="wrap">
<h1>AI Hub · 观测看板</h1>
<div class="sub">统一指标表 metrics（source=gateway / bridge / board）· 各通道用量与效果 · 工作舱记忆 · 生成于 {ts} · 30s 自刷新
</div>
<div class="grid">{hero}{funnel_card}</div>
<div class="two">
  <div class="card"><h2>通道热力（近 {d['window_hours']}h）</h2>{chan_html}</div>
  <div class="card"><h2>额度水位 / 数据源心跳</h2>{src_html}
    <div class="muted" style="margin-top:10px;font-size:12px">限流命中见左表；App 通道被限流时桥会记一行 ok=0、note=limit…</div></div>
</div>
<div class="two">
  <div class="card"><h2>客户端用量（谁在干活）</h2>{cli_html}</div>
  <div class="card"><h2>工作舱（项目记忆 / Spec / 产物）</h2>{ws_html}</div>
</div>
<div class="foot">REST：<code>POST /api/metrics</code> 任意客户端自报用量（gateway / codex-cli / 自定义脚本）；
MCP：<code>memory_write/read/list/forget</code> 读写工作舱记忆。数据全在 <code>hub/data/</code>，零云依赖。</div>
</div></body></html>"""
