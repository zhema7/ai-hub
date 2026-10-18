// AI Hub Collab · TraeCode/VS Code 扩展
// 把 AI Hub 共享任务板变成"装上即用"的插件：
//   状态栏实时显示任务板在线状态 → 一键写 MCP 配置到当前项目 → 一键拉起本地服务
// 零依赖：Node 18+ 内置 fetch（undici 不读系统代理，连 127.0.0.1 天然无 502 坑）
const vscode = require("vscode");
const { exec, spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

const SERVICES = [
  { name: "任务板 MCP", port: 4300, path: "/healthz" },
  { name: "LLM 网关", port: 4000, path: "/healthz" },
  { name: "豆包桥", port: 4301, path: "/healthz" },
  { name: "ChatGPT 桥", port: 4302, path: "/healthz" },
];

let statusItem;

function activate(context) {
  statusItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  statusItem.command = "aiHub.checkStatus";
  statusItem.show();
  context.subscriptions.push(statusItem);

  context.subscriptions.push(
    vscode.commands.registerCommand("aiHub.checkStatus", checkStatus),
    vscode.commands.registerCommand("aiHub.writeWorkspaceMcp", writeWorkspaceMcp),
    vscode.commands.registerCommand("aiHub.startBoard", startBoard),
    vscode.commands.registerCommand("aiHub.openBoard", openBoard)
  );

  refreshStatus();
  const timer = setInterval(refreshStatus, 30000); // 每 30s 刷新状态栏
  context.subscriptions.push({ dispose: () => clearInterval(timer) });
}

function boardBase() {
  return vscode.workspace.getConfiguration("aiHub").get("boardUrl") || "http://127.0.0.1:4300";
}

async function refreshStatus() {
  try {
    const r = await fetch(`${boardBase()}/healthz`, { signal: AbortSignal.timeout(3000) });
    const j = await r.json();
    const n = typeof j.open_tasks === "number" ? j.open_tasks : "?";
    statusItem.text = `$(cloud) AI Hub:${n} open`;
    statusItem.tooltip = new vscode.MarkdownString(
      `**AI Hub 任务板在线** · ${n} 个待领任务\n\n点击检查全部服务状态`
    );
    statusItem.backgroundColor = undefined;
  } catch {
    statusItem.text = "$(cloud-offline) AI Hub:offline";
    statusItem.tooltip = "任务板 4300 未在线\n\n点击查看详情 / 启动方法";
  }
}

async function checkStatus() {
  const lines = [];
  for (const s of SERVICES) {
    try {
      await fetch(`http://127.0.0.1:${s.port}${s.path}`, { signal: AbortSignal.timeout(3000) });
      lines.push(`$(pass) ${s.name} (:${s.port}) 在线`);
    } catch {
      lines.push(`$(error) ${s.name} (:${s.port}) 离线`);
    }
  }
  const doc = `### AI Hub 服务状态\n\n${lines.join("  \n")}\n\n` +
    `离线服务在 ai-hub 仓库对应目录跑 start-mcp.ps1 / start.ps1 / start-doubao.ps1 / start-chatgpt.ps1，` +
    `或配置 aiHub.hubRoot 后用「AI Hub: 启动任务板服务」一键拉起。`;
  vscode.window.showInformationMessage(doc, { modal: false }).then(() => {});
  const panel = vscode.window.createWebviewPanel("aiHubStatus", "AI Hub 服务状态", vscode.ViewColumn.Active, {});
  panel.webview.html = `<!DOCTYPE html><html><body style="font-family:var(--vscode-font-family);padding:16px">
    <h2>AI Hub 服务状态</h2><ul style="list-style:none;padding:0;font-size:15px;line-height:2">
    ${lines.map((l) => `<li>${l}</li>`).join("")}</ul>
    <p style="color:var(--vscode-descriptionForeground)">离线服务到 ai-hub 仓库跑对应 start-*.ps1，或配置 aiHub.hubRoot 一键拉起。</p></body></html>`;
  refreshStatus();
}

async function writeWorkspaceMcp() {
  const ws = vscode.workspace.workspaceFolders?.[0];
  if (!ws) return vscode.window.showErrorMessage("没有打开的工作区");
  const dir = path.join(ws.uri.fsPath, ".trae");
  const file = path.join(dir, "mcp.json");
  let cfg = {};
  if (fs.existsSync(file)) {
    try {
      cfg = JSON.parse(fs.readFileSync(file, "utf8"));
    } catch {
      return vscode.window.showErrorMessage(`.trae/mcp.json 解析失败，请手动修复后再试`);
    }
  }
  if (cfg.mcpServers?.["ai-hub-board"]) {
    return vscode.window.showInformationMessage("ai-hub-board 已在当前项目 MCP 配置中，无需重复写入");
  }
  cfg.mcpServers = { ...(cfg.mcpServers || {}), "ai-hub-board": { url: `${boardBase()}/mcp` } };
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(file, JSON.stringify(cfg, null, 2), "utf8");
  vscode.window.showInformationMessage(
    `已写入 ${file}（ai-hub-board → ${boardBase()}/mcp）。重载窗口后生效。`
  );
}

function startBoard() {
  const hubRoot = vscode.workspace.getConfiguration("aiHub").get("hubRoot");
  if (!hubRoot || !fs.existsSync(path.join(hubRoot, "hub", "start-mcp.ps1"))) {
    return vscode.window.showErrorMessage(
      "请先在设置中把 aiHub.hubRoot 指向本地 ai-hub 仓库根目录（含 hub/start-mcp.ps1）"
    );
  }
  const script = path.join(hubRoot, "hub", "start-mcp.ps1");
  spawn("powershell", ["-ExecutionPolicy", "Bypass", "-File", script], {
    detached: true,
    stdio: "ignore",
    cwd: path.join(hubRoot, "hub"),
    windowsHide: true,
  }).unref();
  vscode.window.showInformationMessage("任务板启动中（hub/start-mcp.ps1）… 10 秒后状态栏会自动刷新");
  setTimeout(refreshStatus, 10000);
}

function openBoard() {
  vscode.env.openExternal(vscode.Uri.parse(`${boardBase()}/api/tasks`));
}

function deactivate() {}

module.exports = { activate, deactivate };
