# -*- coding: utf-8 -*-
# AI Hub · 通道成本与节省账本（纯 stdlib，三个 venv 通用）
#
# 核心概念：
#   est_tokens       App/会员通道无真 token，按字符估算（中文 ~1 字 ≈ 1-2 token，/2 偏保守）
#   metered_cost     按公开 API 费率折算「等量按量计费要花多少钱」（等效成本）
#   actual_cost      实际支出：free_app=0（边际）、member=0（订阅已付，边际≈0）、metered=真金
#   saved            metered_cost - actual_cost（用免费/会员替按量省下的钱）
#
# 费率来源（公开报价，2025-2026 区间，标记 est.，CNY / 百万 token）：
#   Moonshot kimi-k2.7-code-highspeed : 1.2 / 2.4  （Kimi 开放平台）
#   Moonshot kimi-k3                  : 0.8 / 2.0
#   火山方舟 doubao-seed-2-1-lite     : 0.15 / 0.3
#   火山方舟 doubao-seed-evolving     : 1.2 / 2.4
#   火山方舟 doubao-seed-2-1-pro      : 1.6 / 3.2
#   ChatGPT App（GPT 系列）等效        : 18 / 36    （≈ GPT-4o $2.5/$10，est.）
#   WorkBuddy 免费档（GLM 等效）       : 0.5 / 1.0  （GLM-4 量级，est.）
#
# 订阅价（member 档参考年费，用于「订阅额度消耗」展示，非逐调用扣费）：
#   ChatGPT Plus/Pro 约 $240/年   ·   Kimi Code 会员约 ¥499/年
USD_CNY = 7.2


# (input_cny_per_mtok, output_cny_per_mtok)
RATES: dict[str, dict] = {
    # ---- App 免费额度（边际成本 0） ----
    "doubao-app":    {"rate": (1.6, 3.2), "type": "free",
                      "model": "doubao-seed-2-1-pro 等效"},
    "workbuddy-app": {"rate": (0.5, 1.0), "type": "free",
                      "model": "GLM-4 等效（WorkBuddy 免费档）"},
    # ---- 订阅制（边际≈0，等效按量价用于算「省了多少」） ----
    "chatgpt-app":   {"rate": (18.0, 36.0), "type": "member",
                      "model": "GPT-6.1 Sol 等效（ChatGPT 订阅）",
                      "sub_cny": 1600, "sub_usd": 240},
    "kimi-vip":      {"rate": (1.2, 2.4), "type": "member",
                      "model": "kimi-k2.7-code 等效（Kimi Code 会员 OAuth）",
                      "sub_cny": 499, "sub_usd": 70},
    # ---- 按量 Key（网关 :4000 档位，真金白银） ----
    "gw-coding":     {"rate": (1.2, 2.4), "type": "metered",
                      "model": "kimi-k2.7-code / doubao-seed-evolving"},
    "gw-long":       {"rate": (0.8, 2.0), "type": "metered",
                      "model": "kimi-k3"},
    "gw-think":      {"rate": (0.8, 2.0), "type": "metered",
                      "model": "kimi-k3"},
    "gw-cheap":      {"rate": (0.15, 0.3), "type": "metered",
                      "model": "doubao-seed-2-1-lite"},
}

# LiteLLM 网关档位 model 名 → 通道 id（dispatcher 从 response 取 model 反查用）
MODEL_TO_CHANNEL = {
    "coding": "gw-coding", "long": "gw-long", "think": "gw-think", "cheap": "gw-cheap",
    "doubao-app": "doubao-app", "chatgpt-app": "chatgpt-app",
    "workbuddy-app": "workbuddy-app", "kimi-vip": "kimi-vip", "kimi-vip-hs": "kimi-vip",
}


def info(channel: str) -> dict:
    return RATES.get(channel or "", {})


def estimate_tokens(prompt_chars: int = 0, answer_chars: int = 0) -> tuple[int, int]:
    """无真 token 时按字符估算（中文 1 字 ≈ 1-2 token，/2 偏保守）。"""
    return (max(0, int(prompt_chars or 0)) // 2,
            max(0, int(answer_chars or 0)) // 2)


def metered_cost_cny(channel: str, prompt_tokens: int, completion_tokens: int) -> float:
    """等量按量计费要花多少钱（CNY）。"""
    r = info(channel).get("rate")
    if not r:
        return 0.0
    return r[0] * (prompt_tokens or 0) / 1e6 + r[1] * (completion_tokens or 0) / 1e6


def actual_cost_cny(channel: str, prompt_tokens: int, completion_tokens: int) -> float:
    """实际支出：free/member 边际 0，metered 按费率。"""
    t = info(channel).get("type")
    if t == "metered":
        return metered_cost_cny(channel, prompt_tokens, completion_tokens)
    return 0.0


def saved_cny(channel: str, prompt_tokens: int, completion_tokens: int) -> float:
    """用免费/会员替按量省下的钱。"""
    return metered_cost_cny(channel, prompt_tokens, completion_tokens) \
        - actual_cost_cny(channel, prompt_tokens, completion_tokens)


def to_usd(cny: float) -> float:
    return (cny or 0.0) / USD_CNY


def subscription(channel: str) -> dict | None:
    """member 档订阅信息（用于展示订阅额度消耗，非逐调用扣费）。"""
    r = info(channel)
    if r.get("type") == "member" and "sub_cny" in r:
        return {"cny": r["sub_cny"], "usd": r["sub_usd"], "model": r.get("model", "")}
    return None


def channel_label(channel: str) -> str:
    r = info(channel)
    return r.get("model", channel or "—")
