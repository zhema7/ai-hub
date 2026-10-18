# -*- coding: utf-8 -*-
# AI Hub · 统一控制台（GET /）
# 一页聚合：服务体检（TCP 探测各端口）+ 快捷建单（表单直发 /api/tasks）+
# 任务看板（六列 + open 任务表）+ 用量观测摘要（复用 dashboard.collect）+ 工作舱。
# 零依赖服务端 HTML，30s 自刷新；指标明细仍在 /dashboard。
import html
import socket
import sqlite3

import dashboard

SERVICES = [("LiteLLM 网关", 4000), ("Kimi 会员桥", 4100), ("豆包桥", 4301),
            ("ChatGPT 桥", 4302), ("ChatGPT CDP", 9222), ("豆包 CDP", 9223)]

KINDS = ["chat", "writing", "coding", "long", "reasoning", "multimodal", "bulk", "realtime", "task"]


def _probe(port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _open_tasks(conn) -> list:
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id,title,status,kind,project,priority,claimed_by FROM tasks "
            "WHERE status IN ('open','doing','review') ORDER BY status DESC, priority DESC, created_at LIMIT 12"
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def collect() -> dict:
    d = dashboard.collect()
    d["services"] = [("MCP 任务板", 4300, True)] + [(n, p, _probe(p)) for n, p in SERVICES]
    conn = sqlite3.connect(str(dashboard.DB), timeout=10)
    try:
        d["active_tasks"] = _open_tasks(conn)
    finally:
        conn.close()
    return d


_CSS_EXTRA = """
.chips{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}
.chip{display:flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;
border:1px solid var(--line);font-size:12px;background:var(--card)}
.dot{width:8px;height:8px;border-radius:50%}
.up{background:var(--free);box-shadow:0 0 6px var(--free)}
.down{background:#3a4152}
form{display:grid;grid-template-columns:1fr 1fr;gap:8px}
form .full{grid-column:1/-1}
input,select,textarea{background:#0f1218;border:1px solid var(--line);border-radius:6px;
color:var(--fg);padding:7px 9px;font:inherit;font-size:13px}
textarea{resize:vertical;min-height:56px}
button{background:var(--acc);color:#fff;border:0;border-radius:6px;padding:8px 16px;
font:inherit;cursor:pointer}
button:hover{filter:brightness(1.15)}
.cols{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px}
.col{flex:1;min-width:90px;background:#10131a;border:1px solid var(--line);border-radius:8px;
padding:8px 10px;text-align:center}
.col b{font-size:20px;display:block}
.col span{font-size:11px;color:var(--mut);text-transform:uppercase}
"""


def render(d: dict) -> str:
    e = html.escape
    cnt = d["funnel"]
    sv = d["savings"]

    chips = "".join(
        f'<span class="chip"><span class="dot {"up" if ok else "down"}"></span>{e(n)} :{p}</span>'
        for n, p, ok in d["services"])

    kind_opts = "".join(f'<option value="{k}">{k}</option>' for k in KINDS)
    form = f"""
<form method="post" action="/api/tasks">
  <input class="full" name="title" placeholder="任务标题（必填）" required>
  <textarea class="full" name="instructions" placeholder="任务指令（必填）：要做什么、产物要求、引用的 Spec/文件" required></textarea>
  <input name="project" placeholder="project（如 ai_hub_demo）">
  <select name="kind">{kind_opts}</select>
  <input name="output_dir" placeholder="产物直落目录（绝对路径，可空）">
  <input name="priority" type="number" value="0" title="优先级，越大越先调度">
  <div class="full"><button type="submit">创建任务</button>
  <span class="muted" style="font-size:12px;margin-left:8px">dispatcher 常驻时按 kind 自动路由到免费/会员通道；也可由 Trae 客户端经 MCP 认领</span></div>
</form>"""

    cols = "".join(f'<div class="col"><b>{cnt.get(c, 0)}</b><span>{c}</span></div>' for c in
                   ["blocked", "open", "doing", "review", "done", "failed"])
    if d["active_tasks"]:
        rows = "".join(
            f'<tr><td class="muted">{e(t["id"])}</td><td>{e(t["title"][:40])}</td>'
            f'<td><span class="pill">{e(t["status"])}</span></td><td>{e(t["kind"] or "-")}</td>'
            f'<td>{e(t["project"] or "-")}</td><td class="muted">{e(t["claimed_by"] or "")}</td></tr>'
            for t in d["active_tasks"])
        tasks_html = ('<table><tr><th>id</th><th>任务</th><th>状态</th><th>kind</th><th>项目</th><th>认领方</th></tr>'
                      + rows + "</table>")
    else:
        tasks_html = '<div class="empty">暂无进行中任务——从上方建单，或等 dispatcher 自动认领</div>'

    if d["channels"]:
        mx = max(c["n"] for c in d["channels"])
        chan_rows = "".join(
            f'<tr><td>{dashboard._bar(c["n"], mx, dashboard._cost_color(c["cost_class"]))}</td>'
            f'<td>{e(c["channel"])}</td><td class="num">{c["n"]}</td>'
            f'<td class="num">{c["ok_rate"]:.0f}%</td><td class="num">{c["avg_ms"]:,} ms</td></tr>'
            for c in d["channels"][:6])
        chan_html = ('<table><tr><th></th><th>通道</th><th class="num">调用</th>'
                     '<th class="num">成功</th><th class="num">均延迟</th></tr>' + chan_rows + "</table>")
    else:
        chan_html = '<div class="empty">近 24h 无通道调用</div>'

    ws_rows = "".join(
        f'<tr><td>{e(w["project"])}</td><td class="num">{w["specs"]}</td><td class="num">{w["core"]}</td>'
        f'<td class="num">{w["decision"]}</td><td class="num">{w["episodic"]}</td></tr>'
        for w in d["workspaces"])
    ws_html = ('<table><tr><th>工作舱</th><th class="num">Spec</th><th class="num">core</th>'
               '<th class="num">decision</th><th class="num">episodic</th></tr>' + ws_rows + "</table>"
               if d["workspaces"] else '<div class="empty">暂无工作舱</div>')

    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="30"><title>AI Hub · 控制台</title>
<style>{dashboard._CSS}{_CSS_EXTRA}</style></head><body><div class="wrap">
<h1>AI Hub · 控制台</h1>
<div class="sub">任务 / 通道 / 记忆 一页统管 · 指标明细见 <a href="/dashboard">/dashboard</a> · 30s 自刷新</div>
<div class="chips">{chips}</div>
<div class="grid">
  <div class="card"><h2>快捷建单</h2>{form}</div>
  <div class="card"><h2>近 {d['window_hours']}h 节省 <span class="pill">est.</span></h2>
    <div class="hero"><div class="big">${sv['usd']:.2f}</div>
    <div class="u">{sv['calls']} 次调用 · 等效 {sv['tokens']:,} token · 实际支出 ${sv['spent_usd']:.4f}</div></div>
    <div style="margin-top:12px">{chan_html}</div></div>
</div>
<div class="two">
  <div class="card"><h2>任务看板</h2><div class="cols">{cols}</div>{tasks_html}</div>
  <div class="card"><h2>工作舱</h2>{ws_html}
    <div class="muted" style="margin-top:10px;font-size:12px">记忆工具：memory_write / memory_read /
    memory_list / memory_forget（MCP）· 交付与打回自动沉淀</div></div>
</div>
<div class="foot">REST：POST /api/tasks 建单 · POST /api/metrics 自报用量 · GET /api/board 看板 JSON · GET /healthz</div>
</div></body></html>"""
