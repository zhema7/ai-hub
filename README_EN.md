# AI Hub — Save Your Money 💰

> **Your paid AI quotas deserve an API.**
> ChatGPT subscription, Kimi membership, Doubao desktop free quota — you've already paid
> for these, yet they only work through manual chat UIs because vendors ship no API.
> AI Hub turns them into an **OpenAI-compatible gateway + multi-agent collaboration board**,
> so the cheap/free quotas do the heavy lifting and your expensive agent credits stay untouched.

**中文** | [English](README_EN.md)

## Compliance (read this first)

This project **does not bypass paywalls, does not reverse-engineer protocols, and does not
farm free tiers**. The principles:

- All quotas come from **your own paid subscriptions / your own logged-in desktop apps** —
  AI Hub merely automates what you could already do by hand. It creates no access you don't own.
- Desktop-app channels use the **official Windows UIA accessibility API** and the
  **official Chromium CDP debug interface** — the same public channels used by assistive
  software and test automation. No private protocols, no forged requests.
- Respect each platform's ToS (ChatGPT subscriptions allow reasonable use; abusive
  concurrency gets rate-limited anyway — this project serializes each channel with a lock by design).
- **Personal, single-machine use only** (`127.0.0.1`). Do not expose it as a public service
  or resell quota. You are responsible for your own accounts.

## A real money-saving scenario (our daily workflow)

```
Create a task in Trae/any IDE → ChatGPT's chat answers it (spends the cheap
message quota, NOT the Codex agent-usage pool) → artifacts land in your
project folder → run tests locally
```

**Same job: a Codex task burns 50–200K tokens; a chat conversation costs 5–20K — 5–10× cheaper**,
and Doubao's app quota costs literally nothing. AI Hub turns that manual copy-paste pipeline
into a fully automated one.

Technically: unify every AI quota you own (pay-per-token keys, memberships, desktop-app
free/paid quota) into **OpenAI-compatible model endpoints**, then run
**cross-client, cross-platform multi-agent scheduling** on top. Fully local, `127.0.0.1` only.

## The savings ledger (real savings, not marketing)

| Quota | Before | With AI Hub | Cost |
|---|---|---|---|
| Doubao app free tier | manual chat only | `doubao-app` channel works autonomously (9s/round, CDP background drive) | **$0** |
| ChatGPT subscription chat | manual chat only | `chatgpt-app` channel: copywriting / reasoning / error diagnosis instead of Codex agent tasks | **$0** (saves 5–10× Codex usage) |
| Kimi membership | web/CLI manual | `kimi-vip` OAuth bridge → main model for any OpenAI client | **$0** |
| Expensive IDE agent credits (Trae/Cursor…) | every request costs credits | custom model → AI Hub gateway; **official rule: custom models cost 0 credits** | **$0** |

All of this is quota **you already paid for** — AI Hub charges nothing new. It just unlocks
"manual chat" into "programmable resources".

## Why this route is unique: UIA / CDP desktop-app channels

Every "zero-API-cost" project on GitHub (gpt4free, openclaw-zero-token, AIClient2API…) only has
two routes: **web sessions** (cookies expire, heavy web risk control) or **protocol
reverse-engineering** (ban-prone, compliance risk).

We take a third route — the **official accessibility / debug interfaces of desktop apps**:

| Channel | Tech | Why it's stable |
|---|---|---|
| Doubao desktop | **CDP** (UIA via winapp kept as fallback) | official debug port, login persists, no web risk control |
| ChatGPT desktop | **CDP** (`--remote-debugging-port`, ProseMirror via execCommand) | typing/reading with **zero foreground stealing** |

A global GitHub search for "uiautomation llm agent" / "gui automation chatbot api" finds
**no comparable project** — the desktop-app channel is our exclusive route. 9 classes of
real-world pitfalls (contenteditable typing, UI re-render invalidation, reply-completion
gating, full-width punctuation conversion…) are all solved and documented.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ Any AI client: Trae / WorkBuddy / Codex CLI / your own agent    │
│  OpenAI protocol :4000 │ MCP board :4300 │ Skill packages        │
└──────────────────────────┬──────────────────────────────────────┘
                  hub dispatch layer
            Kanban (SQLite ledger + artifacts → portable across clients)
            Capability routing: model strengths × cost priority
                          │ OpenAI-compatible
               ┌──────────▼──────────┐
               │ ai-relay gateway    │  LiteLLM Proxy, tiers + fallback
               └──┬───┬───┬───┬───┬──┘
   pay-per-token │ Kimi │ Volcano │ Kimi │ Desktop-app channels:
   keys          │ open │  Ark    │ OAuth│ Doubao CDP :4301
                 │ plat │         │ :4100│ ChatGPT CDP :4302
                 └──────┴─────────┴──────┴────────────────────
```

## Milestones (all shipped)

- **P1** ChatGPT desktop channel (`:4302`, subscription chat quota)
- **P2** Doubao background-ified — 25–40s → **9s/round**, zero foreground stealing
- **P3** Kanban multi-agent board — state machine `blocked→open→doing→review→done`,
  dependencies, subtasks, review feedback loop, project-level Spec sharing, 14 MCP tools
- **P4** Capability routing — `kind` × cost tier × per-kind rank, dispatcher with fallback
  chains, artifacts auto-delivered, success enters review flow
- **P5** Skill-based delivery — repo ships `.trae/skills/ai-hub-collab` so any Trae client
  opening the repo instantly knows the collaboration protocol
- **Extension** — `extension/ai-hub-collab-0.1.0.vsix` (TraeCode/VS Code): status-bar task
  monitor, one-click MCP config, service health panel. Install from VSIX.
- **One-click Trae link** — paste in browser address bar:
  `trae://trae.ai-ide/mcp-import?type=http&name=ai-hub-board&config=eyJ1cmwiOiJodHRwOi8vMTI3LjAuMC4xOjQzMDAvbWNwIn0=`

## Signature workflow: let ChatGPT's chat do Codex's job

One `task_create` call from any MCP client:

```json
{
  "title": "Write an LRU-cached rate-limiter function for utils.py",
  "instructions": "Pure function + usage example, full code block",
  "kind": "reasoning",
  "output_dir": "C:\\your-trae-project\\docs\\aihub"
}
```

Dispatcher: claims it → **chatgpt-app channel** (subscription message quota, Codex usage = 0)
→ artifact written to `output_dir/aihub-<taskid>.md` → enters review. Read the file in your
IDE, run tests, file the next task with the error if any.

IDE credits work the same way: Settings → Models → Add custom model →
`http://127.0.0.1:4000/v1` — **custom models cost 0 credits** per the official billing rules.

## Quick start

```powershell
copy ai-relay\.env.example ai-relay\.env   # optional: MOONSHOT_API_KEY / ARK_API_KEY
.\ai-relay\start.ps1                        # gateway :4000
.\ai-relay\start-kimi-vip.ps1               # optional Kimi membership :4100
.\hub\start-doubao.ps1                      # optional Doubao desktop :4301
.\hub\start-chatgpt.ps1                     # optional ChatGPT desktop :4302
.\hub\start-mcp.ps1                         # optional MCP board :4300
```

Client config snippets: `ai-relay/clients/` (Codex CLI), `hub/clients/` (Trae / WorkBuddy MCP).
