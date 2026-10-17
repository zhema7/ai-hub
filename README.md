# AI Hub — 个人多智能体工作台

把手里所有 AI 额度（开放平台按量 Key、会员订阅、桌面 App 免费/会员额度）统一抽象成
**OpenAI 兼容的模型接口**，再在其上做多智能体调度。本机私有部署，全部走 `127.0.0.1`。

```
┌────────────────────────────────────────────────────────────┐
│ 下游客户端：Codex CLI / WorkBuddy / Trae / 自研 Agent        │
└──────────────────────────┬─────────────────────────────────┘
                           │ OpenAI 兼容协议
                ┌──────────▼──────────┐
                │  ai-relay 网关 :4000 │  LiteLLM Proxy
                │ 档位 cheap/coding/   │  多平台打散 + fallback
                │ think/long + App 档  │
                └──┬───┬───┬───┬─────┘
        ┌──────────┘   │   │   └───────────────┐
┌───────▼──────┐ ┌─────▼────┐ ┌───────────┐ ┌──▼──────────────┐
│ Kimi 开放平台 │ │ 火山方舟  │ │ Kimi 会员桥 │ │ 豆包 App 执行器  │
│ (按量 Key)   │ │ (按量 Key)│ │ :4100      │ │ :4301           │
└──────────────┘ └──────────┘ │ OAuth 套餐 │ │ GUI 自动化,零 Key│
                              └────────────┘ └─────────────────┘
┌────────────────────────────────────────────────────────────┐
│ hub：任务调度层（任务板 MCP :4300 / doubao 执行器 / Web UI）  │
└────────────────────────────────────────────────────────────┘
```

## 目录

| 路径 | 说明 |
|---|---|
| [ai-relay/](ai-relay) | LiteLLM 统一网关（:4000）+ Kimi 会员 OAuth 适配桥（:4100） |
| [hub/](hub) | 调度层：豆包 App 执行器 + OpenAI 适配桥（:4301）、任务板 MCP Server（:4300） |
| [多智能体工作台调研报告.md](多智能体工作台调研报告.md) | 方案选型调研（为什么是这个架构） |

## 本方案的优点

1. **把"没有 API 的额度"变成可编程资源**
   Kimi 会员（OAuth 套餐）、豆包 App 会员/免费额度本来只能在官方客户端里用，
   本方案通过会员桥（REST）和 GUI 自动化（winapp UIA）把它们封装成标准
   `/v1/chat/completions`，**不额外花一分钱 API 费用**。

2. **一个接口，多家平台**
   下游只写"能力档位"（cheap / coding / think / long），网关在同档位的
   Kimi、豆包（火山方舟）之间自动打散、失败自动降级（fallback 链），
   客户端完全不感知平台差异。

3. **GUI 自动化通道是可靠的工程方案，不是玩具**
   豆包执行器解决了 contenteditable 打字、UI 重渲染 slug 失效、回复完成
   三道闸（回显确认 → 会话页确认 → chips 出现）、全角标点转换等 9 类实测坑，
   端到端 ~25-40s/轮，稳定可用。

4. **完全本地私有**
   所有服务监听 127.0.0.1，API Key 只存在本机 `.env`（不入库），
   对话不经过任何第三方中转。

5. **MCP 标准化的多智能体协作**
   hub 内置任务板 MCP Server（Streamable HTTP + 旧版 SSE 双协议），
   Trae / WorkBuddy 等任意 MCP 客户端可直接认领/回报任务，
   产物落盘可追溯（artifacts + 任务账本 SQLite）。

6. **踩坑文档化**
   代理截胡（HTTP_PROXY=7993）、venv 启动器失效、Chromium UIA 树暴露条件、
   MSIX 应用 CDP 开启方法等环境坑全部记录在案，换机可复现。

## 快速开始

```powershell
# 1. 填 Key（开放平台按量部分，可选）
copy ai-relay\.env.example ai-relay\.env   # 填 MOONSHOT_API_KEY / ARK_API_KEY

# 2. 启动统一网关（:4000）
.\ai-relay\start.ps1

# 3. 可选：Kimi 会员通道（:4100）
.\ai-relay\start-kimi-vip.ps1

# 4. 可选：豆包 App 通道（:4301，需已登录的豆包桌面端）
.\hub\start-doubao.ps1

# 5. 可选：任务板 MCP（:4300）
.\hub\start-mcp.ps1
```

下游客户端配置片段见 `ai-relay/clients/`（Codex CLI）与 `hub/clients/`（Trae / WorkBuddy MCP）。

## 进行中

- ChatGPT 桌面端接入：已验证 `--remote-debugging-port=9222` 可开启 CDP 并执行 JS
  （MSIX 应用、ProseMirror 输入框），正在封装后台执行器（打字/读回复不抢前台焦点）。
- 豆包执行器后台化（CDP / 剪贴板+WM_PASTE 方案选型中）。
