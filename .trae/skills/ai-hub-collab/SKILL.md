---
name: ai-hub-collab
description: 接入 AI Hub 共享任务板进行多智能体协作。当需要认领任务、交付产物、拆分子任务、处理 review 返工、跨端接力或与 Dispatcher 自动调度配合时使用。不用于单会话内的普通问答。
---

# AI Hub 多智能体协作

AI Hub 把"任务即数据"落到本地：看板账本在 SQLite（hub/data/board.db），产物在 hub/data/artifacts/，项目记忆在 hub/data/workspaces/<project>/（工作舱）。换端/换智能体/换机器任务现场不丢。你作为协作智能体，通过 MCP 任务板认领和交付任务。

## 接入

MCP 服务 `ai-hub-board`（127.0.0.1:4300），二选一写入客户端 MCP 配置：

- 现代客户端（Streamable HTTP）：`{"type":"http","url":"http://127.0.0.1:4300/mcp"}`
- 旧版客户端（SSE）：`{"type":"sse","url":"http://127.0.0.1:4300/sse"}`

参考配置片段在 `hub/clients/trae-mcp.json` 和 `hub/clients/workbuddy-mcp.json`。任务板不在线时先启动 `hub/start-mcp.ps1`。

## 协作协议（认领 → 干活 → 交付 → 评审）

1. `task_board` 或 `task_list` 找 `open` 任务（`blocked` 是依赖未就绪，别碰）。
2. `task_claim(task_id, client_name)` 原子认领，认领成功即拿到完整 instructions；被抢就换下一个，不要重试硬抢。
3. 干活前 `spec_read(project)` 读项目级 Spec（若有），严格遵循。
4. 产物一律 `artifact_write` 落账本（代码、文档、清单），不要在回话里塞长文本。
5. `task_report(task_id, "done", result_summary)` 交付——任务进 `review` 列等待评审，不是直接 done。
6. 被打回（回到 `doing` 且带 feedback）时重新认领同一任务，按 feedback 修改后再次交付。

## 任务类型与路由（配合 Dispatcher 自动调度）

任务 `kind` 决定执行通道，成本优先级 App 免费额度 > 会员订阅 > 按量 Key：

| kind | 通道 |
|---|---|
| coding | kimi-vip（Kimi k2.7 会员桥） |
| long | kimi-vip（1M 上下文） |
| writing / chat | doubao-app（豆包桌面 CDP，免费额度，~9s/轮） |
| reasoning / multimodal | chatgpt-app（ChatGPT 桌面 CDP，订阅额度，~130s/轮） |
| bulk / realtime | 按量兜底 / grok-web（规划） |

你创建任务时（`task_create`）必须写准 `kind` 和 `project`：写对了 `kind`，常驻的 Dispatcher（`hub/start-dispatcher.ps1`）会自动认领并路由到最低成本通道执行；写错就只能人工认领。`priority` 数字越大越先被调度。

## 拆分与依赖

- 大任务用 `task_create` 带 `parent_id` 拆子任务；子任务全 done 父任务才可交付。
- 有先后关系的任务用 `depends_on`（任务 id 列表）；依赖未完成自动 `blocked`，全部 done 后自动解锁进 `open`。

## 评审职责

你是 reviewer 时用 `task_review(task_id, approve, feedback)`：通过 → `done`；打回 → 自动退回 `doing` 并附 feedback，原认领者返工。评审先看产物（`artifact_list` / `artifact_read`）再裁决，不要只看 result_summary。

## 工作舱记忆（项目上下文不丢）

一个 `project` = 一个**工作舱** `hub/data/workspaces/<project>/`（`spec/` + `memory.db` + `skills/`），跨客户端、跨机器、跨会话共享。干活前除 `spec_read` 外，先 `memory_read(project, query)` 召回相关上下文：

- 记忆分三层：`core`（项目铁律/技术栈/约束，随任务常驻注入）、`decision`（"为什么选 A 不选 B"、评审打回结论）、`episodic`（交付摘要、踩过的坑）。
- 有价值的结论用 `memory_write(project, layer, content, tags, key)` 沉淀：`core` 传 `key` 可同名覆盖，`decision`/`episodic` 追加。写错了用 `memory_list` + `memory_forget(id)` 清理。
- Dispatcher 会自动记：交付成功写 `episodic`，`task_review` 打回写 `decision`，通过则把该单 `episodic` 标记 verified——流水账不用你手动记，但**项目铁律/重要决策要主动写** `core`/`decision`。
- 用量与效果看板：`http://127.0.0.1:4300/dashboard`（通道调用/成功率/平均延迟/限流命中/返工率/客户端/工作舱）。

## 注意

- `task_claim` 的 `client_name` 用稳定身份（如 "trae-ide"、"workbuddy"），便于追溯谁领了哪单。
- 产物文件名写清楚（result.md / design.md / diff.patch），同一任务可写多个产物。
- 本地回环服务，任何 HTTP 调用前先确认代理变量已清（HTTP_PROXY/HTTPS_PROXY），否则会被 127.0.0.1:7993 截胡。
