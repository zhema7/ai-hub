# -*- coding: utf-8 -*-
# AI Hub · P6 统一指标层
# 三层数据源（gateway 网关账本 / bridge 桥层遥测 / board 看板账本）都写同一张 metrics 表，
# 看板只读这一张表 + tasks。纯 sqlite3，零依赖，三个 venv 通用。
#
# 写入方：
#   dispatcher.finish()      → source=board   （任务粒度：通道/延迟/字符/ok/rework）
#   doubao_bridge/chatgpt_bridge → source=bridge（App 通道真值：延迟/字符/限流命中）
#   POST /api/metrics        → 任意客户端自报（source 由上报方指定，如 gateway / codex-cli）
#
# App 通道无真 token 概念 → est_tokens = (prompt_chars + answer_chars) / 2 估算，看板标注 est.
import sqlite3
import time
from pathlib import Path

import cost

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "board.db"

_DDL = """CREATE TABLE IF NOT EXISTS metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL, source TEXT, channel TEXT, kind TEXT, project TEXT, task_id TEXT,
  ok INTEGER, latency_ms INTEGER, prompt_chars INTEGER, answer_chars INTEGER,
  est_tokens INTEGER, prompt_tokens INTEGER, completion_tokens INTEGER,
  cost_class TEXT, cost_usd REAL, saved_usd REAL, note TEXT)"""

FIELDS = ["ts", "source", "channel", "kind", "project", "task_id", "ok", "latency_ms",
          "prompt_chars", "answer_chars", "est_tokens", "prompt_tokens", "completion_tokens",
          "cost_class", "cost_usd", "saved_usd", "note"]

# 就地加列（P8 计费增强）：旧库只有 est_tokens/cost_usd，缺 prompt_tokens/completion_tokens/saved_usd
_ADD_COLS = [
    ("prompt_tokens", "INTEGER DEFAULT 0"),
    ("completion_tokens", "INTEGER DEFAULT 0"),
    ("saved_usd", "REAL DEFAULT 0"),
]


def ensure(conn: sqlite3.Connection):
    conn.execute(_DDL)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(metrics)").fetchall()}
    for name, decl in _ADD_COLS:
        if name not in cols:
            conn.execute(f"ALTER TABLE metrics ADD COLUMN {name} {decl}")


def build_row(*, source: str, channel: str = "", kind: str = "", project: str = "",
              task_id: str = "", ok: int = 1, latency_ms: int = 0, prompt_chars: int = 0,
              answer_chars: int = 0, est_tokens: int | None = None,
              prompt_tokens: int = 0, completion_tokens: int = 0,
              cost_class: str = "", cost_usd: float = 0.0, saved_usd: float = 0.0,
              note: str = "", ts: float | None = None) -> list:
    if est_tokens is None:
        est_tokens = int((prompt_chars + answer_chars) / 2)
    # 未显式提供 token 时按字符估算（App/会员通道无真 token；REST 自报只给字符也能估算）
    if not prompt_tokens and not completion_tokens:
        prompt_tokens = int((prompt_chars or 0) / 2)
        completion_tokens = int((answer_chars or 0) / 2)
    # 未显式提供成本时按通道费率折算（caller 给了 channel+token 即可自动记账）
    if not cost_usd and not saved_usd:
        cost_usd = cost.to_usd(cost.actual_cost_cny(channel, prompt_tokens, completion_tokens))
        saved_usd = cost.to_usd(cost.saved_cny(channel, prompt_tokens, completion_tokens))
    return [ts if ts is not None else time.time(), source or "", channel or "", kind or "",
            project or "", task_id or "", 1 if ok else 0, int(latency_ms or 0),
            int(prompt_chars or 0), int(answer_chars or 0), int(est_tokens or 0),
            int(prompt_tokens or 0), int(completion_tokens or 0),
            cost_class or "", float(cost_usd or 0.0), float(saved_usd or 0.0),
            (note or "")[:300]]


def log(conn: sqlite3.Connection, **kw):
    """在既有连接上写一行（dispatcher / MCP 板共用，避免额外加锁）。"""
    ensure(conn)
    conn.execute(f"INSERT INTO metrics ({','.join(FIELDS)}) VALUES ({','.join('?' * len(FIELDS))})",
                 build_row(**kw))


def record(**kw):
    """独立短连接写入（桥 / REST 摄入端用）。遥测失败绝不影响主流程。"""
    conn = sqlite3.connect(DB, timeout=10)
    try:
        conn.execute("PRAGMA busy_timeout=10000")
        log(conn, **kw)
        conn.commit()
    except Exception as e:
        print(f"[metrics] 写入失败（忽略）: {type(e).__name__}: {e}")
    finally:
        conn.close()
