# -*- coding: utf-8 -*-
# AI Hub · P7 项目工作舱 · 记忆库
# 一个 project = 一个工作舱：hub/data/workspaces/<project>/{spec,artifacts,skills,memory.db}
# 换客户端 / 换机器 / 换会话，只要 hub/data 在，现场全在（对齐 PilotDeck WorkSpace 模型）。
#
# 记忆分层（对齐 Letta / MemGPT）：
#   core      核心事实（项目铁律/技术栈/约束），每次任务都注入，key 同名覆盖
#   decision  决策（"为什么选 A 不选 B"、评审打回结论），按 kind/关键词召回
#   episodic  情节（每单交付摘要、踩过的坑），FTS5 top-k 召回
#
# 检索用内置 SQLite FTS5，中文按「单字切分 + 短语匹配」做子串检索（unicode61 对整段中文
# 会合成一个 token 无法子串命中，故索引里存切分副本 seg，查询也切分成短语）。
# 纯 sqlite3 零依赖，MCP 板（hub venv）与 dispatcher（ai-relay venv）通用。
import re
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKS = ROOT / "data" / "workspaces"
LAYERS = ("core", "decision", "episodic")

_DDL = [
    """CREATE TABLE IF NOT EXISTS memory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        layer TEXT, key TEXT DEFAULT '', content TEXT, tags TEXT DEFAULT '',
        status TEXT DEFAULT 'active', weight INTEGER DEFAULT 0, task_id TEXT DEFAULT '',
        created_at REAL, updated_at REAL)""",
    "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(content, seg, tokenize='unicode61')",
]


def _safe(project: str) -> str:
    s = re.sub(r"[\\/:*?\"<>|\s]+", "_", (project or "default").strip())
    return s.strip("._") or "default"


def workspace_dir(project: str, create: bool = True) -> Path:
    """工作舱目录：spec/ artifacts/ skills/（memory.db 由 _connect 落文件）。"""
    d = WORKS / _safe(project)
    if create:
        for sub in ("spec", "artifacts", "skills"):
            (d / sub).mkdir(parents=True, exist_ok=True)
    return d


def spec_dir(project: str, create: bool = True) -> Path:
    """项目 Spec 目录（P3 spec_write 的新家；旧 _specs 由 mcp_server 一次性迁移）。"""
    d = workspace_dir(project, create) / "spec"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def _connect(project: str) -> sqlite3.Connection:
    workspace_dir(project)   # 确保目录存在
    conn = sqlite3.connect(WORKS / _safe(project) / "memory.db", timeout=10)
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    for ddl in _DDL:
        conn.execute(ddl)
    return conn


# ---------------- 中文友好的 FTS5 切分 ----------------
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def _seg(s: str) -> str:
    """CJK 逐字拆开、拉丁/数字连写保留，供 unicode61 逐字建索引。"""
    out, buf = [], []
    for ch in s or "":
        if _CJK.match(ch):
            if buf:
                out.append("".join(buf)); buf = []
            out.append(ch)
        elif ch.isalnum() or ch == "_":
            buf.append(ch)
        else:
            if buf:
                out.append("".join(buf)); buf = []
    if buf:
        out.append("".join(buf))
    return " ".join(out)


def _fts_query(s: str, mode: str = "any") -> str:
    """构造 FTS5 查询。
    mode=phrase：整串作为相邻短语（精确子串，适合 memory_read 精确查）；
    mode=any：CJK 相邻二元组 + 拉丁词各自成项、OR 组合（召回优先，适合自动注入）。"""
    toks = _seg(s).split()
    if not toks:
        return ""
    if mode == "phrase":
        return '"' + " ".join(toks) + '"'
    terms, i = [], 0
    while i < len(toks):
        t = toks[i]
        if (len(t) == 1 and _CJK.match(t) and i + 1 < len(toks)
                and len(toks[i + 1]) == 1 and _CJK.match(toks[i + 1])):
            terms.append('"' + t + " " + toks[i + 1] + '"')   # 重叠二元组：滑动 1 位
        else:
            terms.append('"' + t + '"')
        i += 1
    return " OR ".join(dict.fromkeys(terms))


def _index(conn, mid: int, content: str):
    conn.execute("DELETE FROM memory_fts WHERE rowid=?", (mid,))
    conn.execute("INSERT INTO memory_fts(rowid,content,seg) VALUES(?,?,?)", (mid, content, _seg(content)))


def _row(r) -> dict:
    return dict(r) if r is not None else {}


# ---------------- 写入 / 检索 / 管理 ----------------
def write(project: str, layer: str, content: str, tags: str = "", key: str = "",
          task_id: str = "", status: str = "active", weight: int = 0) -> dict:
    """写一条记忆。core + key 非空 → 同名覆盖（保留 id）；decision/episodic 追加。"""
    layer = (layer or "episodic").strip()
    if layer not in LAYERS:
        return {"ok": False, "msg": f"layer 只能是 {'/'.join(LAYERS)}"}
    if not (content or "").strip():
        return {"ok": False, "msg": "content 为空"}
    conn = _connect(project)
    try:
        now = time.time()
        if layer == "core" and key:
            old = conn.execute("SELECT id FROM memory WHERE layer='core' AND key=?", (key,)).fetchone()
            if old:
                conn.execute("UPDATE memory SET content=?,tags=?,status=?,weight=?,task_id=?,updated_at=? WHERE id=?",
                             (content, tags, status, int(weight), task_id, now, old["id"]))
                _index(conn, old["id"], content)
                conn.commit()
                return {"ok": True, "id": old["id"], "layer": layer, "key": key, "updated": True}
        cur = conn.execute("""INSERT INTO memory (layer,key,content,tags,status,weight,task_id,created_at,updated_at)
                              VALUES (?,?,?,?,?,?,?,?,?)""",
                           (layer, key, content, tags, status, int(weight), task_id, now, now))
        mid = cur.lastrowid
        _index(conn, mid, content)
        conn.commit()
        return {"ok": True, "id": mid, "layer": layer, "key": key, "updated": False}
    finally:
        conn.close()


def search(project: str, query: str, layer: str = "", limit: int = 5, mode: str = "any") -> list:
    """FTS5 检索（中文子串友好）。query 为空时退化为列最近记忆。
    mode=any 召回优先（二元组 OR）；mode=phrase 精确子串。"""
    conn = _connect(project)
    try:
        if not (query or "").strip():
            return list_layer(project, layer)[:limit]
        q = _fts_query(query, mode)
        if not q:
            return []
        sql = ("SELECT m.id,m.layer,m.key,m.content,m.tags,m.status,m.weight,m.task_id,m.created_at,"
               "bm25(memory_fts) AS score FROM memory_fts JOIN memory m ON m.id=memory_fts.rowid "
               "WHERE memory_fts MATCH ?")
        args: list = [q]
        if layer:
            sql += " AND m.layer=?"
            args.append(layer)
        sql += " ORDER BY score ASC LIMIT ?"
        args.append(int(limit))
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def list_layer(project: str, layer: str = "") -> list:
    conn = _connect(project)
    try:
        if layer:
            rows = conn.execute("SELECT * FROM memory WHERE layer=? ORDER BY weight DESC, updated_at DESC", (layer,))
        else:
            rows = conn.execute("SELECT * FROM memory ORDER BY layer, weight DESC, updated_at DESC")
        return [dict(r) for r in rows.fetchall()]
    finally:
        conn.close()


def core(project: str) -> list:
    return list_layer(project, "core")


def forget(project: str, mid: int) -> dict:
    conn = _connect(project)
    try:
        cur = conn.execute("DELETE FROM memory WHERE id=?", (int(mid),))
        conn.execute("DELETE FROM memory_fts WHERE rowid=?", (int(mid),))
        conn.commit()
        return {"ok": bool(cur.rowcount), "id": int(mid), "msg": "" if cur.rowcount else "记忆不存在"}
    finally:
        conn.close()


def mark_verified(project: str, task_id: str) -> int:
    """review 通过 → 该单 episodic 标记 verified（权重更高，召回更靠前）。"""
    conn = _connect(project)
    try:
        cur = conn.execute("UPDATE memory SET status='verified', weight=1, updated_at=? "
                           "WHERE layer='episodic' AND task_id=?", (time.time(), task_id))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def context(project: str, query: str = "", k: int = 5, max_chars: int = 320) -> str:
    """给 dispatcher.build_prompt 用的记忆注入片段（核心常驻 + 相关 top-k）。"""
    blocks = []
    cores = core(project)
    if cores:
        blocks.append("[核心记忆]\n" + "\n".join(f"- {r['content'][:max_chars]}" for r in cores))
    if (query or "").strip():
        rel = [r for r in search(project, query, limit=k + 3) if r["layer"] != "core"][:k]
        if rel:
            blocks.append("[相关记忆]\n" + "\n".join(
                f"- ({r['layer']}) {r['content'][:max_chars]}" for r in rel))
    return "\n\n".join(blocks)
