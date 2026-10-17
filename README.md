# AI Hub — 个人多智能体工作台

把手里所有 AI 额度（开放平台按量 Key、会员订阅、桌面 App 免费/会员额度）统一抽象成
**OpenAI 兼容的模型接口**，再在其上做**跨客户端、跨平台的多智能体协作调度**。
本机私有部署，全部走 `127.0.0.1`。

## 核心差异化：UIA / CDP 桌面 App 通道

市面上所有"零 API 成本"项目（gpt4free、openclaw-zero-token、AIClient2API 等）只有两条路：
**Web 会话**（cookie 易过期、网页风控严格）或**协议逆向**（随时被封、有合规风险）。

本方案走的是第三条路——**桌面 App 官方无障碍/调试接口**：

| 通道 | 技术 | 为什么更稳 |
|---|---|---|
| 豆包桌面端 | **winapp UIA**（Windows 官方无障碍 API） | 不逆向任何协议，登录态持久，无 Web 风控；发送按钮/回复区全是官方暴露的 UI 语义 |
| ChatGPT 桌面端 | **CDP**（Chromium 远程调试，`--remote-debugging-port`） | 直接执行 JS 操作 DOM（ProseMirror 输入框），打字/读回复**完全不抢前台焦点** |

GitHub 全局搜索 "uiautomation llm agent" / "gui automation chatbot api" **无同类项目**——
桌面 App 通道是本方案的独占路线。9 类实测坑（contenteditable 打字、UI 重渲染、
回复完成判定三道闸、全角标点转换等）全部解决并文档化，端到端 ~25-40s/轮。

## 架构

```
┌─────────────────────────────────────────────────────────────────┐
│ 任意 AI 客户端：Trae / WorkBuddy / Codex CLI / 自研 Agent          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐   │
│  │ OpenAI 协议   │  │ MCP 任务板    │  │ Skill 包（能力插件）  │   │
│  │ /v1/chat/... │  │ :4300 认领/回报│  │ 开发规范/管线/工作流 │   │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘   │
└─────────┼──────────────────┼─────────────────────┼───────────────┘
          │                  │                     │
   ┌──────▼──────────────────▼─────────────────────▼──────┐
   │ hub 调度层                                            │
   │  看板任务板（SQLite 账本 + artifacts 落盘 → 换端不丢） │
   │  能力路由：按模型优劣势 + 成本优先级分配任务            │
   └──────────────────────┬───────────────────────────────┘
                          │ OpenAI 兼容协议
               ┌──────────▼──────────┐
               │  ai-relay 网关 :4000 │  LiteLLM Proxy
               │ 档位 cheap/coding/   │  多平台打散 + fallback
               │ think/long + App 档  │
               └──┬───┬───┬───┬───┬──┘
   ┌─────────┐ ┌──▼───┐ ┌─▼─────┐ ┌▼────────┐ ┌▼─────────────┐
   │按量 Key  │ │Kimi  │ │火山方舟│ │Kimi 会员桥│ │桌面 App 通道  │
   │OpenAI 等 │ │开放平台│ │(豆包)  │ │OAuth:4100│ │豆包UIA :4301 │
   └─────────┘ └──────┘ └───────┘ └─────────┘ │ChatGPT CDP:4302│
                                               │(规划中) Gemini/ │
                                               │Grok/GLM web 桥  │
                                               └────────────────┘
```

## 目录

| 路径 | 说明 |
|---|---|
| [ai-relay/](ai-relay) | LiteLLM 统一网关（:4000）+ Kimi 会员 OAuth 适配桥（:4100） |
| [hub/](hub) | 调度层：豆包 App 执行器 + 适配桥（:4301）、任务板 MCP Server（:4300） |
| [多智能体工作台调研报告.md](多智能体工作台调研报告.md) | 方案选型调研（为什么是这个架构） |

## 竞品分析

| 项目 | ★ | 路线 | 与本方案的关系 |
|---|---|---|---|
| BerriAI/litellm | 61k | API Key 聚合网关 | 本方案的底座（网关层直接用） |
| QuantumNous/new-api | 50k | API Key 聚合分发 | 只做 Key 管理，不解决"额度无 API" |
| songquanpeng/one-api | 37k | 同上 | 同上 |
| justlovemaki/AIClient2API | 8.9k | 客户端私有协议逆向 → OpenAI 接口 | 思路相近，但协议逆向有封号/合规风险 |
| linuxhsj/openclaw-zero-token | 5.2k | Web 会话驱动 13 家官方网页版 | 最接近；但 cookie 易过期、网页风控严格 |
| duolahypercho/codex-router | 3.9k | Codex 外挂模型路由 + Kimi OAuth | 本方案 kimi-vip 桥 + 档位的子集 |
| xtekky/gpt4free | 67k | 免费渠道大杂烩 | 稳定性差，偏灰色 |
| phodal/routa | 1.8k | 看板式多智能体协调（MCP/ACP/A2A） | hub 任务板演进参照（见路线图 P3） |
| **本方案** | — | **桌面 App UIA/CDP 通道 + 档位路由 + MCP 任务板** | UIA/CDP 路线 GitHub 无同类 |

## 本方案的优点

1. **把"没有 API 的额度"变成可编程资源** — Kimi 会员（OAuth）、豆包/ChatGPT App 额度，
   封装成标准 `/v1/chat/completions`，不额外花一分钱 API 费用。
2. **合规且抗风控** — UIA/CDP 是官方接口，不逆向协议、不碰 Web 会话 cookie。
3. **一个接口，多家平台** — 能力档位路由 + fallback 链，客户端无感知。
4. **任务状态与客户端解耦** — 任务即数据（SQLite + artifacts 落盘），
   换智能体/换应用/换机器，任务现场不丢。
5. **完全本地私有** — 127.0.0.1，Key 只存本机 `.env`。
6. **踩坑文档化** — 代理截胡、UIA 树暴露条件、MSIX CDP 开启法等全部在案。

## 路线图

### P1 · 平台扩展：照"会员桥"模式铺满主流平台
参照 openclaw-zero-token 的 13 家清单，每家的接入方式按"最稳通道"选型：

| 平台 | 通道 | 状态 |
|---|---|---|
| Kimi | OAuth 会员桥 :4100 | ✅ 已上线 |
| 豆包 | 桌面 App UIA :4301 | ✅ 已上线 |
| ChatGPT | 桌面 App CDP :4302 | 🔧 已验证可行性（DOM/输入框/发送链全部探明） |
| Gemini / Grok / GLM / DeepSeek / Qwen | Web 会话桥（浏览器 Cookie 一次登录，照 kimi_bridge 模式） | 📋 待做 |
| OpenAI /  Anthropic 等按量 Key | 网关直挂 | 📋 有 Key 即挂 |

### P2 · 豆包执行器后台化
CDP 优先（豆包同为 Electron），备选剪贴板 + WM_PASTE。目标：打字/读回复零前台抢占。

### P3 · 看板式多智能体协作（参照 routa）
任务板升级为项目级看板，实现"**任意 AI 客户端跨端、跨平台共同完成一个项目**"：
- **任务即数据**：看板状态机（backlog → doing → review → done）存 SQLite，
  产物落 artifacts/ —— 智能体/应用/机器怎么切换，任务现场都在。
- **认领-回报协议**：已有 MCP 任务板（:4300，Trae/WorkBuddy 已接入）扩展
  任务依赖、子任务拆分、review 回流。
- **Spec 共享**：项目级 spec/design 文档作为 artifact 在智能体间传递上下文。

### P4 · 能力路由：最低成本实现最高效开发
任务板认领时按"模型优劣势 × 成本"双维路由：

| 任务类型 | 首选通道 | 原因 |
|---|---|---|
| 编码 Agent 主力 | Kimi k2.7-code / doubao-seed-evolving | 官方 Coding 专用，高速 |
| 长文档/架构设计 | Kimi k3 (1M) | 超长上下文 + 深度推理 |
| 中文创作/小红书文案 | 豆包 App 档 | 中文表达强，花 App 免费额度 |
| 复杂推理/多模态 | ChatGPT App 档 | GPT 系推理，花订阅额度 |
| 批量分类/摘要/格式化 | doubao lite (按量) | RPM 30000 高并发便宜 |
| 实时热点信息 | Grok web 桥（规划） | 自带 X 实时数据 |

成本优先级：**App 免费额度 > 会员订阅 > 按量 Key**；档位 fallback 链兜底可用性。

### P5 · Skill 化交付
把开发规范、HD-2D 管线、发布流程等沉淀为 Skill 包挂在任务上，
智能体认领任务时自动加载对应 Skill —— 人只定义标准和验收，执行全自动化。

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

- **ChatGPT 桌面端接入（P1）**：CDP 已连通，页面/输入框（ProseMirror）/发送按钮全部探明，
  正在封装执行器（execCommand insertText 后台打字 → 点发送 → 轮询回复 DOM）。
- **豆包执行器后台化（P2）**：CDP / 剪贴板+WM_PASTE 方案选型中。
