# AI Hub — Save Your Money 💰

> **你已付费的 AI 额度，值得一个 API。**
> ChatGPT 订阅、Kimi 会员、豆包 App 免费额度——这些钱已经花出去了，
> 却因为没有官方 API 而只能手动聊天。AI Hub 把它们全部变成
> **OpenAI 兼容接口 + 多智能体协作看板**，让便宜/免费的额度干重活，
> 昂贵的 agent 用量一分不花。

**[English](README_EN.md)** | 中文

## 合规声明（先读这条）

本项目**不绕任何付费墙、不逆向任何协议、不批量薅免费额度**。原则是：

- 所有额度都来自**你自己已付费的订阅/已登录的桌面 App**——AI Hub 只是把你本来就能手动干的活自动化，不创造任何你不拥有的访问权。
- 桌面 App 通道走的是 **Windows 官方 UIA 无障碍接口**和 **Chromium 官方 CDP 调试接口**（与辅助功能软件、自动化测试同一条公开通道），不碰私有协议、不伪造请求。
- 使用请遵守各平台服务条款（ChatGPT 订阅条款允许合理使用，滥用式并发同样会限流——本项目内置单通道串行锁，天然防滥用）。
- **仅供个人单机使用**（127.0.0.1），请勿用于对外提供服务/转售。由此引发的任何账号问题，使用者自担。

一个真实的省钱场景（也是本项目的日常工作流）：

```
在 Trae/任意 IDE 里建任务 → ChatGPT 的 chat 对话出结果（花的是便宜的消息额度，
不是 Codex 的 agent 用量池）→ 产物自动落进项目文件夹 → 本地跑测试
```

**同样的活，Codex 一次任务消耗 50-200K tokens；chat 对话只要 5-20K——省 5-10 倍用量**，
而豆包这类 App 额度更是零成本。AI Hub 把这条人肉搬运链路做成了全自动管道。

技术上：把手里所有 AI 额度（开放平台按量 Key、会员订阅、桌面 App 免费/会员额度）统一抽象成
**OpenAI 兼容的模型接口**，再在其上做**跨客户端、跨平台的多智能体协作调度**。
本机私有部署，全部走 `127.0.0.1`。

## 省钱账单（为什么这是真省，不是噱头）

| 额度 | 原来只能 | 接入 AI Hub 后 | 成本 |
|---|---|---|---|
| 豆包 App 免费额度 | 手动聊天 | `doubao-app` 通道自动干活（9s/轮，CDP 后台驱动） | **¥0** |
| ChatGPT 订阅 chat 消息 | 手动聊天 | `chatgpt-app` 通道：写文案/推理/诊断报错，替代 Codex agent 任务 | **¥0**（省下 5-10× 的 Codex 用量） |
| Kimi 会员订阅 | 网页/CLI 手动用 | `kimi-vip` OAuth 桥 → 任意 OpenAI 客户端当主力模型 | **¥0** |
| 昂贵的 IDE agent 积分（Trae/Cursor 等） | 内置模型每请求扣积分 | 自定义模型指向 AI Hub 网关，**官方规则：自定义模型 0 积分** | **¥0** |

全部是**你已经付过费**的额度——AI Hub 不产生任何新费用，只是把它们从"手动聊天"
解锁成"可编程资源"。合规路线：桌面 App 走官方 UIA 无障碍接口 / CDP 调试接口，不逆向协议。

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
| 豆包 | 桌面 App CDP :4301（UIA 版保留作 fallback） | ✅ 已上线（P2 后台化完成，9s/轮零抢焦点） |
| ChatGPT | 桌面 App CDP :4302 | ✅ 已上线（订阅 chat 额度，`reasoning` 类任务首选通道） |
| Gemini / Grok / GLM / DeepSeek / Qwen | Web 会话桥（浏览器 Cookie 一次登录，照 kimi_bridge 模式） | 📋 待做 |
| OpenAI /  Anthropic 等按量 Key | 网关直挂 | 📋 有 Key 即挂 |

### P2 · 豆包执行器后台化 —— ✅ 已完成
豆包桌面端支持 `--remote-debugging-port=9223`，UIA 抢焦点路线全部换 CDP 注入：
`data-streaming` 属性直读流式状态，完成判定靠 streaming=false + 文本稳定 + suggest chips。
单轮 25-40s → **9s**，零前台抢占。

### P3 · 看板式多智能体协作（参照 routa）—— ✅ 首期已完成
任务板升级为项目级看板，实现"**任意 AI 客户端跨端、跨平台共同完成一个项目**"：
- **任务即数据**：看板状态机（blocked → open/backlog → doing → review → done / failed）存 SQLite，
  产物落 artifacts/ —— 智能体/应用/机器怎么切换，任务现场都在。
- **认领-回报协议**：MCP 任务板（:4300，Trae/WorkBuddy 已接入）已扩展
  任务依赖（depends_on 未完成自动 blocked，解锁自动进 open）、子任务拆分（parent_id）、
  review 回流（task_report review → task_review 通过/打回带 feedback 返工）。
- **Spec 共享**：项目级 spec/design 文档经 spec_write / spec_read / spec_list 在智能体间传递上下文。
- 工具面：14 个 MCP 工具（新增 task_create / task_board / task_subtasks / task_review /
  spec_write / spec_read / spec_list），REST 同步扩展 + `/api/board`；
  kind / priority 字段已为 P4 路由预留。验证：`hub\verify_mcp.py` 12 步全过。

### P4 · 能力路由：最低成本实现最高效开发 —— ✅ 首期已完成
任务按 `kind` 经"**模型优劣势 × 成本**"双维路由（`hub/routing.yaml` 通道表 +
`hub/router.py` 引擎 + `hub/dispatcher.py` 调度守护）：
dispatcher 轮询看板 open 任务 → 原子认领 → 按 fallback 链逐通道执行 →
产物落 artifacts、任务进 review 走 P3 评审流（全链失败才 failed）。

| 任务类型 | 首选通道 | 原因 |
|---|---|---|
| 编码 Agent 主力 | Kimi k2.7-code / doubao-seed-evolving | 官方 Coding 专用，高速 |
| 长文档/架构设计 | Kimi k3 (1M) | 超长上下文 + 深度推理 |
| 中文创作/小红书文案 | 豆包 App 档 | 中文表达强，花 App 免费额度 |
| 复杂推理/多模态 | ChatGPT App 档 | GPT 系推理，花订阅额度 |
| 批量分类/摘要/格式化 | doubao lite (按量) | RPM 30000 高并发便宜 |
| 实时热点信息 | Grok web 桥（规划） | 自带 X 实时数据 |

成本优先级：**App 免费额度 > 会员订阅 > 按量 Key**；同成本按 rank 排序，
通道掉线自动退次选，空链任务置 failed。prompt 自动拼入项目 Spec（P3）与上轮 review 反馈。
验证：`hub\verify_router.py`（离线单测 6 断言 + doubao-app 端到端 + 空链兜底）全过。

### P5 · Skill 化交付 —— ✅ 首期已完成
协作接入已沉淀为项目 Skill（`.trae/skills/ai-hub-collab/SKILL.md`）：
任何 Trae 客户端打开本仓库即自动获得协作协议——MCP 接入配置、
认领→交付→review 流程、kind 路由表、子任务/依赖拆分规范。
网关 `chatgpt-app`、`doubao-app` 双 App 档位已挂（`ai-relay/config.yaml`），
任意 OpenAI 客户端经 `:4000` 直调桌面 App 额度。
环境解耦：MCP 板用独立 `hub/.venv`（mcp<2），与网关 venv（mcp 2.x）彻底解互斥。
后续：HD-2D 管线、发布流程等领域 Skill 挂任务自动加载（方向不变）。

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

# 5. 可选：ChatGPT App 通道（:4302，需已登录的 ChatGPT 桌面端）
.\hub\start-chatgpt.ps1

# 6. 可选：任务板 MCP（:4300）
.\hub\start-mcp.ps1
```

下游客户端配置片段见 `ai-relay/clients/`（Codex CLI）与 `hub/clients/`（Trae / WorkBuddy MCP）。

### 签名工作流：让 ChatGPT 的 chat 替你干 Codex 的活

把"chat 对话 → 结果落项目文件夹"全自动化的标准姿势（Trae / 任意 MCP 客户端）：

```json
{
  "title": "给 utils.py 写一个带 LRU 缓存的限流函数",
  "instructions": "纯函数实现 + 使用示例，输出完整代码块",
  "kind": "reasoning",
  "output_dir": "C:\\你的Trae项目\\docs\\aihub"
}
```

Dispatcher 自动：认领 → **chatgpt-app 通道**（花订阅 chat 消息额度，Codex 用量 0 消耗）→
产物写 `output_dir/aihub-<任务id>.md` → 进 review。IDE 里直接读文件、跑测试，报错再建下一单。

IDE 自带模型积分同理：在 Trae 设置 → 模型 → 添加自定义模型，URL 填
`http://127.0.0.1:4000/v1`——**自定义模型 0 积分**，日常问答/轻任务全部免费。

## 已完成里程碑

- **P1 ChatGPT 桌面通道 / P2 豆包后台化 / P3 看板协作 / P4 能力路由 / P5 Skill 化交付 全部上线**，
  详见下方路线图各节的技术细节。
- **TraeCode/VS Code 扩展**（`extension/ai-hub-collab-0.1.0.vsix`）：状态栏任务监控、
  一键写 MCP 配置、服务体检——Install from VSIX 即装即用。
- **Trae 一键接入链接**（免装扩展）：浏览器地址栏粘贴
  `trae://trae.ai-ide/mcp-import?type=http&name=ai-hub-board&config=eyJ1cmwiOiJodHRwOi8vMTI3LjAuMC4xOjQzMDAvbWNwIn0=`
  回车，Trae 自动完成任务板配置。
