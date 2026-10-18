# AI Hub Collab

把 [AI Hub](https://github.com/zhema7/ai-hub) 共享任务板接入 TraeCode / VS Code —— 多智能体协作的"装上即用"入口。

## 功能

- **状态栏监控**：实时显示任务板（:4300）在线状态与待领任务数，30s 自动刷新
- **一键写配置**：`AI Hub: 写入当前项目的 MCP 配置` —— 把 `ai-hub-board`（Streamable HTTP）写进当前项目 `.trae/mcp.json`，重载窗口即接入 14 个协作工具
- **一键拉服务**：配置 `aiHub.hubRoot` 指向本地 ai-hub 仓库后，`AI Hub: 启动任务板服务` 自动后台拉起 MCP 板
- **服务体检**：`AI Hub: 检查服务状态` 同时探测任务板/网关/豆包桥/ChatGPT 桥四个本地服务
- **打开看板**：`AI Hub: 打开任务板` 直达 REST 看板

## 使用前提

本地克隆 [ai-hub 仓库](https://github.com/zhema7/ai-hub)，并至少启动任务板：

```powershell
cd hub && .\start-mcp.ps1
```

## 协作协议速览

1. `task_board` 看 `open` 任务（`blocked` 是依赖未就绪）
2. `task_claim(task_id, client_name)` 原子认领
3. `spec_read(project)` 读项目 Spec，按 instructions 干活
4. 产物 `artifact_write` 落账本
5. `task_report(done)` 交付 → 进 review → `task_review` 评审
6. `kind` 决定执行通道：writing/chat→豆包、reasoning→ChatGPT、coding→Kimi（Dispatcher 自动调度）

详细协议见仓库内 `.trae/skills/ai-hub-collab/SKILL.md`。

## 已知坑

本机若开着系统代理（如 uniclash 7993），IDE 内连本地 MCP 可能 502 —— 系统环境变量加一条
`NO_PROXY=127.0.0.1,localhost` 后重载窗口即可。

## License

MIT
