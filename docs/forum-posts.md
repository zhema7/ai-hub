# AI Hub 论坛发布文案包

仓库：https://github.com/zhema7/ai-hub
定位：Save Your Money —— 把已付费的 AI 额度变成可编程 API + 多智能体协作看板

> 使用建议：每篇都带真实数字（9s/轮、5-10×、¥0），先答“省多少”再答“怎么实现的”。
> 被问合规就引用仓库 README 的合规声明，不要回避。

---

## 1. V2EX（中文，技术分享区）

**标题**：做了个小工具：把 ChatGPT/豆包/Kimi 的会员额度变成 OpenAI 兼容 API，省了我 5-10 倍的 Codex 用量

**正文**：

痛点：手里三个 AI 订阅（ChatGPT Plus、Kimi 会员、豆包免费），钱花了，但都只能手动聊天——没有 API。另一边 Codex 的 agent 用量池烧得飞快，一次任务 50-200K tokens。

后来发现一个事实：**同一个任务，让 ChatGPT 的 chat 对话来做只要 5-20K tokens，是 Codex agent 任务的 1/5 到 1/10**。于是做了 AI Hub：

- 桌面 App 通道：豆包走 Windows 官方 UIA 无障碍接口，ChatGPT/豆包走 Chromium 官方 CDP 调试接口——不逆向协议，不碰 cookie，登录态持久，后台驱动不抢前台焦点（9 秒一轮）
- 统一网关：所有通道封装成 `/v1/chat/completions`，任意 OpenAI 客户端直接调
- MCP 任务板：任务即数据（SQLite），多个 AI 客户端（Trae/WorkBuddy/Codex CLI）跨端认领同一个项目的任务，换机器不丢
- 能力路由：写文案自动走豆包（免费）、推理自动走 ChatGPT chat（订阅消息额度）、编码走 Kimi——按成本自动 fallback

现在我的日常：IDE 里建一条任务 → ChatGPT 桌面 App 自己在后台动起来 → 结果文件直接出现在项目文件夹里 → 本地跑测试。Codex 用量一个月几乎没动。

全部本机 127.0.0.1，Key 只存本地 .env。合规声明写在 README 第一条：不绕付费墙、不逆向协议、只用你自己已付费的额度、内置串行锁防滥用、个人单机使用。

踩过的坑（全在仓库文档里）：ProseMirror 输入框打字的三种姿势、UI 重渲染后 UIA 元素失效、回复完成判定的三道闸、豆包 tiptap 半角转全角……

欢迎拍砖。如果你觉得“这也算违规吗”，先说结论：它做的每一件事你手动都能做，只是不用人肉搬运了。

---

## 2. linux.do（中文，AI 话题）

**标题**：[分享] AI Hub — 已付费订阅的 API 化 + 多智能体看板，ChatGPT chat 替 Codex 干活省 5-10 倍用量

**正文**：

佬友们应该都有这个痛：订阅买了一堆，API 额度还得另外充。AI Hub 解决的就是这个缝：

1. **订阅额度 API 化**：ChatGPT 桌面端走 CDP（`--remote-debugging-port`，execCommand 打字零抢焦点）、豆包桌面端走 CDP/UIA、Kimi 会员走 OAuth 桥——全部封装成 OpenAI 兼容端点
2. **多智能体协作看板**：MCP 协议任务板，Trae/WorkBuddy 连进来就能认领任务，状态存 SQLite，换端不丢；支持子任务拆分、依赖阻塞、review 回流
3. **能力路由**：按“模型优劣势 × 成本”自动分配——免费 App 额度 > 会员订阅 > 按量 Key，掉线自动 fallback
4. **产物直落项目文件夹**：任务带 `output_dir` 参数，交付时结果自动写进你的 IDE 项目目录

关键数据：豆包通道 9 秒/轮后台跑完；ChatGPT chat 做生成型任务是 Codex agent 的 1/5-1/10 用量；Trae 自定义模型官方规则 0 积分。

合规部分认真写在了 README：UIA/CDP 是官方公开接口，不逆向协议；只用你自己已登录已付费的额度；单通道串行锁天然防滥用；纯本机部署。

目前还在快速迭代，Gemini/Grok/GLM web 桥在路上。求 star 求试用求拍砖。

---

## 3. Reddit r/ChatGPTCoding（英文）

**Title**: I built a local tool that turns your ChatGPT subscription into an OpenAI-compatible API — cut my Codex usage by 5-10×

**Body**:

The problem: I pay for ChatGPT Plus, but there's no API for it. Meanwhile my Codex usage pool was burning through 50-200K tokens per agent task.

Then I noticed: **the same job done via the chat conversation costs 5-20K tokens — 1/5 to 1/10 of an agent task**. So I built AI Hub:

- Desktop-app channels via **official interfaces only**: ChatGPT desktop via Chromium CDP (`--remote-debugging-port`, typing through ProseMirror's execCommand — zero foreground stealing, ~10s/round), Doubao via Windows UIA accessibility API. No protocol reverse-engineering, no cookie juggling.
- Everything wrapped as `/v1/chat/completions` — any OpenAI client can call it
- An MCP kanban board so multiple AI clients (Trae, WorkBuddy, Codex CLI) can claim tasks from the same project — SQLite-backed, nothing lost when you switch machines
- Capability routing: writing → free Doubao app quota, reasoning → ChatGPT chat, coding → Kimi membership. Cost-ordered with automatic fallback.

My daily workflow now: create a task in my IDE → the ChatGPT desktop app answers it in the background → the result file lands directly in my project folder → I run tests locally. My Codex usage has barely moved this month.

**On compliance** (front and center in the README): no paywall bypass, no protocol RE — only quotas you already paid for and are already logged into; a per-channel serial lock prevents abusive concurrency; 127.0.0.1 only, personal use.

Repo: https://github.com/zhema7/ai-hub — pitfalls and verification scripts included. Happy to answer questions.

---

## 4. Reddit r/LocalLLaMA（英文，偏技术受众）

**Title**: Turn paid AI app quotas into a local OpenAI gateway via official UIA/CDP interfaces (no protocol RE, no cookies)

**Body**:

Existing "zero-cost" projects (gpt4free, web-session bridges, protocol RE projects) all share the same fragility: cookies expire, web risk control escalates, private protocols break. AI Hub takes a different route — **drive the vendors' own desktop apps through official accessibility/debug interfaces**:

- ChatGPT desktop: CDP on the bundled Chromium. Page target is the internal `app://` shell; input is ProseMirror (`execCommand('insertText')` works in background); completion detection via turn markers + streaming state.
- Doubao desktop: CDP (winapp UIA as fallback) — `data-streaming` attribute gives you the streaming state directly.
- Kimi membership: local OAuth bridge to its web REST.

On top of that: an MCP kanban (task-as-data in SQLite, artifacts on disk) so heterogeneous agents can collaborate on one project, plus capability/cost routing across channels with fallback chains.

Measured: Doubao round-trip 9s background; chat-as-worker costs 1/5–1/10 the tokens of an agentic coding loop for generation-shaped tasks.

Not for everyone (Windows + you must own the subscriptions), but if you're already paying, this is found money: https://github.com/zhema7/ai-hub

---

## 5. Show HN（攒 100+ star 后发）

**Title**: Show HN: AI Hub – turn your paid AI subscriptions into an OpenAI-compatible API and multi-agent board

**Body**（首段即核心叙事，英文短句）：

I pay for ChatGPT Plus, Kimi, and use Doubao's free tier. None of them ship an API — the money buys a chat box and nothing else.

AI Hub gives them one. It drives the vendors' desktop apps through official interfaces (Windows UIA accessibility, Chromium CDP), wraps everything as OpenAI-compatible endpoints, and adds an MCP kanban so multiple AI clients can collaborate on one project. A dispatcher routes each task to the cheapest capable channel: free app quota → subscription → pay-per-token, in that order.

The headline saving: generation-shaped tasks via ChatGPT's chat cost 5–20K tokens vs. 50–200K for the same job in the Codex agent pool. That's 5–10× off, using quota I already paid for.

Compliance is the first section of the README: no paywall bypass, no protocol reverse-engineering, personal single-machine use, per-channel serialization.

Repo: https://github.com/zhema7/ai-hub — verification scripts and a pitfall log included. Feedback welcome.

---

## 6. X/Twitter（build in public，分条发）

**第一条（钩子）**：
I was paying for 3 AI subscriptions and still buying API credits.
Then I realized: the quotas were already mine — they just had no API.
So I built one. ChatGPT desktop now answers coding tasks in the background while my Codex usage sits at zero.
Open source: https://github.com/zhema7/ai-hub 🧵

**第二条（数字）**：
The math that made me build this:
Codex agent task: 50–200K tokens
Same job via chat: 5–20K tokens
Doubao app quota: free
That's not optimization. That's a different cost regime.

**第三条（技术路线）**：
No protocol reverse-engineering. No cookie scraping.
ChatGPT desktop → Chromium CDP (official debug interface)
Doubao desktop → Windows UIA (official accessibility API)
9s/round, zero foreground stealing. Your app just… works in the background.

**第四条（协作）**：
Bonus: an MCP kanban board on top.
Trae, Codex CLI, and any MCP client can claim tasks from the same project. State lives in SQLite, artifacts on disk. Switch machines, the board survives.

---

## 发帖检查清单

- [ ] 仓库已设为 public（发帖前必查）
- [ ] README 首屏有定位句 + 合规声明 + 省钱表（已就位）
- [ ] 每个帖子附仓库链接，格式照上文
- [ ] 准备好 GIF 后（Trae 建单 → App 自动跑 → 文件落盘 30s 录屏）回帖补充，转化率翻倍
- [ ] Reddit 先看各版 self-promotion 规则（r/ChatGPTCoding 允许 project showcase，r/LocalLLaMA 周六 Self-Promotion 帖更稳）
