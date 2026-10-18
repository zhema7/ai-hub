# AI Hub 优化前瞻：观测层 + 记忆层（P6 / P7）

> 目标：让「用哪些客户端、走了哪些通道、花了多少额度、效果如何」全部可见；
> 让「做项目时的 Spec / 决策 / 上下文」跨客户端、跨机器、跨会话不丢。
> 参考 2026 年近期开源项目的成熟范式，但坚持零云依赖、本地优先。
>
> **实现状态（2026-10-18）：P6a/P6b/P6d + P7a/P7b 已全部落地并验证通过**
> （`hub/metrics_log.py`、`hub/dashboard.py`、`hub/memory_store.py`；桥与 dispatcher 已埋点；
> MCP 板新增 `/dashboard` + `POST /api/metrics` + 4 个记忆工具；`hub/verify_metrics.py` ALL PASS）。
> P6c（LiteLLM 原生 `/ui` 用量页）按设计保持可选、默认不开。

---

## 一、调研：近期开源项目在做什么

### 1.1 智能体记忆（Agent Memory）

| 项目 | 核心范式 | 对 AI Hub 的启示 |
|---|---|---|
| **Mem0**（~65k★） | 记忆抽取 + 向量检索，聊天/助手记忆 | 记忆写入必须**自动**，不能靠人手动记 |
| **Zep / Graphiti** | 时序知识图谱（bi-temporal），长期会话 | 记忆要带**时间线**，能回答"什么时候决定的" |
| **Cognee** | 图谱原生 ECL 管线，14 种检索模式 | 检索模式可插拔，别锁死一种 |
| **Letta (MemGPT)** | 分层记忆（核心/归档/召回），自我编辑 | 分层：**核心事实**常驻上下文，**长尾**按需召回 |
| **LangMem** | LangChain 生态记忆策略 | 记忆要有**写策略**（何时写/改写/淘汰） |
| **mcp-memory-service / 官方 memory server** | MCP 原生、本地 SQLite/知识图谱、多客户端共享 | **MCP 工具形态**正是我们的接入方式；一个文件多客户端共享 |
| **清华 PilotDeck（2026-10）** | **WorkSpace 工作舱**：每项目独立文件系统 + 记忆库 + 技能集，任务成本降 83% | 与我们的 **project** 概念完全同构——把 project 升级为"工作舱" |

**共识**：存储不是壁垒，**排序/召回**才是；记忆必须自动写入、分重要性、跨会话可查。

### 1.2 LLM 观测 / 用量看板（Observability）

| 项目 | 形态 | 对 AI Hub 的启示 |
|---|---|---|
| **Langfuse**（MIT，自托管） | OTel 原生 trace，token/成本/评估，自定义看板 | 指标要按 **observation 类型**分维度（generation/embedding） |
| **Helicone**（OSS） | 网关 + 观测一体，0% 加价 | "网关即观测点"——我们的 `:4000` 就是天然埋点处 |
| **LiteLLM Proxy 内置** | 每请求记账 → `/ui?page=new_usage` 用量页 | **开箱即得**：开 `database_url` 即有 spend logs / 按 key·team·tag 聚合 |

**共识**：网关是埋点黄金位；成本与用量必须**按维度聚合**（模型/通道/项目/客户端）。

---

## 二、P6：观测层（用量与效果看板）

### 2.1 三层数据源（各有各的真值）

| 层 | 数据源 | 记录什么 | 真实性 |
|---|---|---|---|
| **L1 网关账本** | LiteLLM `:4000` 内置 spend logs（需开 `database_url`） | 每次 `/v1/chat/completions` 的 model、tokens、cost、latency | 按量 Key 档**真实 token/成本**；App 通道为估算 |
| **L2 桥层遥测** | `doubao_bridge.py :4301` / `chatgpt_bridge.py :4302` | CDP 通道特有：轮次延迟、prompt/answer 字符、限流命中、串行等待 | App 通道的**真值**（无 token 概念，用字符+耗时替代） |
| **L3 看板账本** | `board.db`（tasks/artifacts） | 任务粒度：kind、通道、review 打回、返工轮次 | 协作与**效果**的真值 |

> 核心设计：**统一指标表 `metrics`**，三层都往里写一行（带 `source` 字段），看板只读这一张表 + tasks。

### 2.2 指标表结构（board.db 新增，就地迁移）

```sql
CREATE TABLE IF NOT EXISTS metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL,                 -- 发生时间
  source TEXT,             -- gateway | bridge | board
  channel TEXT,            -- doubao-app / chatgpt-app / kimi-vip / cheap ...
  kind TEXT,               -- 任务能力类型
  project TEXT,
  task_id TEXT,
  ok INTEGER,              -- 1/0
  latency_ms INTEGER,
  prompt_chars INTEGER, answer_chars INTEGER,
  est_tokens INTEGER,      -- 字符/2 估算（App 通道）；按量档用真实值
  cost_class TEXT,         -- free_app | member | metered
  cost_usd REAL,           -- 按量档真实成本；其余 0
  note TEXT
);
```

### 2.3 看板面板（`/dashboard`，由 MCP 板 :4300 内嵌提供）

1. **今日节省**（hero 数字）：`free_app + member` 通道完成的任务数 × 等效按量成本 ≈ 省下多少钱
2. **通道热力图**：各通道 24h 的调用量 / 成功率 / 平均延迟（豆包 9s vs ChatGPT 130s 一眼可见）
3. **任务漏斗**：backlog→doing→review→done + **rework 率**（review 打回占比 = 效果指标）
4. **客户端在线**：Trae / WorkBuddy / Codex CLI / dispatcher 最近心跳
5. **额度水位**：各通道是否被限流（ChatGPT 限流文案命中次数）

技术选型：**零依赖**——服务端拼 HTML + 内联 SVG/`uPlot`（单文件 CDN 可换本地），不引入前端构建链。

### 2.4 落地步骤

- **P6a**：`metrics` 表 + `dispatcher.finish()` 写指标（通道/延迟/字符/ok/rework）
- **P6b**：两个桥各加 3 行埋点（POST 前后记延迟、异常记限流）
- **P6c（可选）**：`ai-relay/config.yaml` 开 `general_settings.database_url` + `store_model_in_db`，启用 LiteLLM 原生 `/ui` 用量页
- **P6d**：`/dashboard` 页面 + `verify_metrics.py`（造数据 → 断言聚合正确）

---

## 三、P7：记忆层（项目上下文不丢失）

### 3.1 "工作舱"模型（对齐 PilotDeck）

一个 **project** = 一个工作舱，含四件套：

```
hub/data/workspaces/<project>/
  ├─ spec/       项目级规范（P3 已有 spec_write，迁到此）
  ├─ memory.db   记忆库（SQLite + FTS5）—— 本层新增
  ├─ artifacts/  任务产物（已有）
  └─ skills/     该项目挂载的 Skill（P5 延伸，预留）
```

换客户端 / 换机器 / 换会话，只要 `hub/data/` 在，**工作舱现场全在**。

### 3.2 记忆分层（对齐 Letta）

| 层 | 内容 | 注入策略 |
|---|---|---|
| **核心记忆 core** | 项目铁律、技术栈、不可违背的约束 | **每次任务都注入**（小、稳定） |
| **决策记忆 decision** | "为什么选 A 不选 B"、评审结论 | 按 `kind` / 标签召回 |
| **情节记忆 episodic** | 每单交付摘要、踩过的坑 | 按关键词 FTS5 召回 top-k |

### 3.3 MCP 工具（新增，与现有 14 工具并列）

| 工具 | 作用 |
|---|---|
| `memory_write(project, layer, content, tags)` | 写记忆（core 覆盖同 key，decision/episodic 追加） |
| `memory_read(project, query, layer, limit)` | FTS5 检索（query 空则列 core） |
| `memory_list(project, layer)` | 列某层全部 |
| `memory_forget(project, id)` | 删除（记忆要可查可改，对齐 PilotDeck 主张） |

### 3.4 自动记忆（关键：不靠人手动记）

- dispatcher 每单**交付成功** → 自动写一条 episodic（kind / 通道 / 产物路径 / 结果摘要前 N 字）
- `task_review` **打回** → 自动写一条 decision（反馈原文 + 任务 id），下次同类任务 prior feedback 自动带上
- review **通过** → 该单 episodic 标记 `verified`（权重更高）

### 3.5 prompt 注入顺序（`dispatcher.build_prompt` 扩展）

```
[工作舱] 项目 X
[核心记忆] ← 常驻
[相关决策/情节记忆] ← FTS5 top-k（用 title+kind 当 query）
[项目 Spec] ← 已有
[任务] title + instructions
[上轮 review 反馈] ← 已有
```

检索用 **SQLite FTS5**（内置，零依赖）；若日后要语义检索，再挂 `sqlite-vec` + 本地 embedding，接口不变。

---

## 四、路线图与顺序

```
P6a 指标表 + dispatcher 埋点  ─┐
P6b 桥层埋点                  ├─→ P6d /dashboard + verify
P6c LiteLLM 原生用量页（可选）─┘
P7a workspaces 目录 + memory 表/FTS5 + 4 个 MCP 工具
P7b build_prompt 注入 + dispatcher 自动记忆 + review 回流
```

先 P6a→P6b→P6d 打通观测，再 P7a→P7b 打通记忆——两者在 `/dashboard` 汇合（看板加"工作舱"卡片）。

---

## 五、风险与边界

- **不引入云依赖**：Langfuse/Helicone 作为**参照**，不作为运行时依赖；自研轻量看板保持 127.0.0.1 私有。
- **App 通道无真 token**：用字符数估算并在看板明确标注"est."，不伪装成精确值。
- **LiteLLM 原生 UI 需 DB**：若非必需，默认不开，避免给 ai-relay 增加迁移负担。
- **记忆膨胀**：episodic 层按项目定期归档；core 层强制小（超长告警）。

---

## 参考来源

- [Persistent Memory Layer for AI Agents 2026 | Cognee](https://www.cognee.ai/blog/guides/open-source-memory-frameworks-llm-agents)
- [Best AI Agent Memory Frameworks in 2026: Compared and Ranked | Atlan](https://atlan.com/know/best-ai-agent-memory-frameworks-2026/)
- [Mem0、Letta、Zep 与 Cognee：四种下注方式](https://menuagentic.com/zh/blogs/mem0-vs-letta-vs-zep-vs-cognee/)
- [清华开源！智能体操作系统任务成本降 83% | 腾讯新闻](http://news.qq.com/rain/a/20261017A0BYE400)
- [Langfuse — Open Source LLM Observability](https://langfuse.com/docs/observability/overview)
- [Helicone — Platform Overview](https://docs.helicone.ai/getting-started/platform-overview)
- [LiteLLM Spend Tracking](https://docs.litellm.ai/docs/proxy/cost_tracking)
- [LiteLLM Customer Usage / Admin UI](https://docs.litellm.ai/docs/proxy/customer_usage)
- [mcp-memory-service（持久共享记忆）](https://pypi.org/project/mcp-memory-service/)
- [@modelcontextprotocol/server-memory（知识图谱记忆）](https://www.npmjs.com/package/@modelcontextprotocol/server-memory)
