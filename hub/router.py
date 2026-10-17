# -*- coding: utf-8 -*-
# AI Hub · P4 路由引擎（纯逻辑，可离线单测）
# route(kind) → 按"能力匹配 × 成本升序 × rank 升序"排出 fallback 链
# 健康探测：enabled 且 endpoint TCP 可连（结果带 TTL 缓存）
import socket
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
CONF = ROOT / "routing.yaml"

_cache: dict = {}   # channel_id -> (ts, alive)
CACHE_TTL = 30      # 秒


def load_config(path: str | Path = CONF) -> dict:
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    assert cfg and "channels" in cfg, "routing.yaml 缺 channels"
    return cfg


def _tcp_alive(endpoint: str, timeout: float = 2.0) -> bool:
    """endpoint 形如 http://127.0.0.1:4301/v1，只探 host:port 是否可连。"""
    try:
        hostport = endpoint.split("://", 1)[1].split("/", 1)[0]
        host, port = hostport.rsplit(":", 1)
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def is_alive(ch: dict, now: float | None = None, probe_fn=_tcp_alive) -> bool:
    """健康探测（带缓存）。probe_fn 可注入假实现做单测。"""
    if not ch.get("enabled"):
        return False
    if not ch.get("endpoint"):
        return False
    now = now if now is not None else time.time()
    ts, alive = _cache.get(ch["id"], (0, False))
    if now - ts < CACHE_TTL:
        return alive
    alive = bool(probe_fn(ch["endpoint"]))
    _cache[ch["id"]] = (now, alive)
    return alive


def route(kind: str, cfg: dict | None = None, healthy_only: bool = True,
          alive_fn=None) -> list[dict]:
    """给任务 kind 排出通道 fallback 链（优先低成本，同成本按 rank）。
    alive_fn(channel)->bool 可注入做单测；默认 is_alive。"""
    cfg = cfg or load_config()
    kind = (kind or "task").strip() or "task"
    if kind == "task":          # 未指定能力类型 → 按通用对话路由
        kind = "chat"
    cost_idx = {c: i for i, c in enumerate(cfg.get("cost_order", []))}
    alive_fn = alive_fn or is_alive

    cands = []
    for ch in cfg["channels"]:
        if kind not in (ch.get("strengths") or []):
            continue
        if healthy_only and not alive_fn(ch):
            continue
        cands.append(ch)
    cands.sort(key=lambda ch: (cost_idx.get(ch.get("cost"), 99),
                               (ch.get("rank") or {}).get(kind, 99)))
    return cands


def describe(kind: str, cfg: dict | None = None) -> str:
    """人读的路由链说明（调试用）。"""
    chain = route(kind, cfg)
    if not chain:
        return f"kind={kind}: 无可用通道"
    return f"kind={kind}: " + " → ".join(
        f"{c['id']}({c['cost']},rank{(c.get('rank') or {}).get(kind, '-')})" for c in chain)


if __name__ == "__main__":
    cfg = load_config()
    for k in ("coding", "long", "writing", "reasoning", "bulk", "realtime", "chat"):
        print(describe(k, cfg))
