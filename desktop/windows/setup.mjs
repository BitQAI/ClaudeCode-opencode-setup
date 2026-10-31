// Claude Desktop × OpenCode Go 一键配置（Windows 版，对应仓库中的 desktop/setup-desktop-opencode.sh）
//
//   node setup.mjs                     安装 / 更新
//   node setup.mjs --key [oc_sk-xxx]   查看 / 设置 OpenCode Go API Key
//   node setup.mjs --port 8799         修改监听端口
//   node setup.mjs --effort max        修改推理强度
//   node setup.mjs --local-title on    标题请求本地应答
//   node setup.mjs --status            查看状态
//   node setup.mjs --restore           还原到安装前状态
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { execFileSync, spawnSync, spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

import * as cfg from "./gateway/ccd_config.mjs";
import * as profile from "./gateway/ccd_profile.mjs";
import * as gateway from "./gateway/ccd_gateway.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const TASK_NAME = "ClaudeDesktopOpenCodeGateway";
const INSTALL_DIR = cfg.stateDir();
const GATEWAY_DIR = path.join(INSTALL_DIR, "gateway");
const CONFIG_FILE = path.join(INSTALL_DIR, "config.json");
const BACKUP_DIR = path.join(INSTALL_DIR, "backup");
const MANIFEST = path.join(BACKUP_DIR, "manifest.txt");

function info(m) { console.log(m); }
function ok(m) { console.log(`✓ ${m}`); }
function warn(m) { console.log(`! ${m}`); }
function die(m) { console.error(`✗ ${m}`); process.exit(1); }

function maskKey(key) {
  return key && key.length > 12 ? `${key.slice(0, 7)}...${key.slice(-4)}` : "***";
}

function copyDir(src, dst) {
  fs.mkdirSync(dst, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const s = path.join(src, entry.name);
    const d = path.join(dst, entry.name);
    if (entry.isDirectory()) copyDir(s, d);
    else fs.copyFileSync(s, d);
  }
}

function ensureBackup() {
  if (fs.existsSync(MANIFEST)) {
    info(`已存在安装前备份（${BACKUP_DIR}），保留不覆盖`);
    return;
  }
  fs.mkdirSync(BACKUP_DIR, { recursive: true });
  const src = profile.claude3pDir();
  const dst = path.join(BACKUP_DIR, "Claude-3p");
  fs.mkdirSync(dst, { recursive: true });
  for (const name of ["configLibrary", "claude_desktop_config.json", "config.json"]) {
    const p = path.join(src, name);
    if (!fs.existsSync(p)) continue;
    if (fs.statSync(p).isDirectory()) copyDir(p, path.join(dst, name));
    else fs.copyFileSync(p, path.join(dst, name));
  }
  const appVersion = detectAppVersion();
  fs.writeFileSync(MANIFEST, [
    `installed_at="${new Date().toISOString()}"`,
    `app_version="${appVersion}"`,
    `claude3p_existed="${fs.existsSync(src) ? "yes" : "no"}"`,
    `task_existed="${taskExists() ? "yes" : "no"}"`,
    `key="${cfg.mask(currentKey())}"`,
  ].join("\n") + "\n", "utf8");
  ok(`已备份到 ${BACKUP_DIR}`);
}

function detectAppVersion() {
  const base = path.join(process.env.LOCALAPPDATA || "", "AnthropicClaude");
  try {
    const dirs = fs.readdirSync(base).filter((d) => d.startsWith("app-")).sort();
    return dirs.length ? dirs[dirs.length - 1].replace(/^app-/, "") : "unknown";
  } catch { return "unknown"; }
}

function currentKey() {
  try { return cfg.loadConfig(CONFIG_FILE).upstream_api_key || ""; } catch { return ""; }
}

function writeConfig(mutator) {
  const config = cfg.loadConfig(CONFIG_FILE);
  mutator(config);
  cfg.writeConfig(config, CONFIG_FILE);
  console.log(`已写入 ${CONFIG_FILE}（Key=${maskKey(config.upstream_api_key)}，端口=${config.port}，effort=${config.default_effort}，标题本地应答=${config.local_title_synthesis ? "on" : "off"}）`);
}

// ---- 启动器（带守护重启的 .cmd） -------------------------------------------
const ENABLED_FLAG = path.join(INSTALL_DIR, "gateway.enabled");

function launcherPath() {
  return path.join(INSTALL_DIR, "start-gateway.cmd");
}

function writeLauncher() {
  const src = path.join(__dirname, "start-gateway.cmd");
  const dst = launcherPath();
  // .cmd 必须是 CRLF 换行，否则 cmd.exe 会解析错乱
  const text = fs.readFileSync(src, "utf8").replace(/\r?\n/g, "\r\n");
  fs.writeFileSync(dst, text, "utf8");
  ok(`启动器：${dst}`);
}

function stopGateway() {
  // 1) 关掉守护循环
  try { fs.unlinkSync(ENABLED_FLAG); } catch { /* ignore */ }
  // 2) 结束当前网关进程
  gateway.stop();
  // 3) 扫尾：干掉残留的网关进程
  spawnSync("powershell", ["-NoProfile", "-Command",
    "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | Where-Object { $_.CommandLine -like '*ccd_gateway.mjs*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
    { encoding: "utf8" });
  try { fs.unlinkSync(path.join(INSTALL_DIR, "gateway.pid")); } catch { /* ignore */ }
}

async function startGateway() {
  fs.writeFileSync(ENABLED_FLAG, "1", "utf8");
  const config = cfg.loadConfig(CONFIG_FILE);
  const detail = await gateway.probe(config);
  if (String(detail).startsWith("200")) {
    ok(`网关已在运行（自检 ${detail}）`);
    return;
  }
  // 启动 Node 守护进程（无控制台窗口）
  const child = spawn(process.execPath, [
    path.join(GATEWAY_DIR, "ccd_gateway.mjs"), "supervise", "--config", CONFIG_FILE,
  ], { detached: true, stdio: "ignore", cwd: INSTALL_DIR, windowsHide: true });
  child.unref();
  for (let i = 0; i < 20; i += 1) {
    await new Promise((r) => setTimeout(r, 500));
    const d = await gateway.probe(config);
    if (String(d).startsWith("200")) {
      ok(`网关已启动（自检 ${d}，守护进程 pid=${child.pid}）`);
      return;
    }
  }
  warn(`网关启动失败，请查看日志：${cfg.logPath()}`);
}

// ---- 开机自启（Windows 上对应 macOS LaunchAgent） --------------------------
function taskExists() {
  const r = spawnSync("schtasks", ["/query", "/tn", TASK_NAME], { encoding: "utf8" });
  return r.status === 0;
}

function installTask() {
  // 任务直接跑 Node 守护进程（无控制台窗口，不会被误按键杀掉）
  const node = process.execPath;
  const cmd = [
    "schtasks", "/create", "/tn", TASK_NAME, "/tr",
    `"${node}" "${path.join(GATEWAY_DIR, "ccd_gateway.mjs")}" supervise --config "${CONFIG_FILE}"`,
    "/sc", "onlogon", "/f",
  ];
  const r = spawnSync(cmd[0], cmd.slice(1), { encoding: "utf8" });
  if (r.status !== 0) {
    warn(`计划任务创建失败：${(r.stderr || "").trim()}`);
    return false;
  }
  ok(`计划任务：${TASK_NAME}（登录时启动网关守护进程，异常自动拉起）`);
  return true;
}

function removeTask() {
  spawnSync("schtasks", ["/delete", "/tn", TASK_NAME, "/f"], { encoding: "utf8" });
}

function installRunKey() {
  // 第二道自启保险：HKCU\...\Run（与计划任务任选其一生效即可）
  const cmd = `"${process.execPath}" "${path.join(GATEWAY_DIR, "ccd_gateway.mjs")}" supervise --config "${CONFIG_FILE}"`;
  const ps = `$k='HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run';` +
    `if (!(Test-Path $k)) { New-Item -Path $k -Force | Out-Null };` +
    `New-ItemProperty -Path $k -Name '${TASK_NAME}' -Value '${cmd.replace(/'/g, "''")}' -PropertyType String -Force | Out-Null;`;
  const r = spawnSync("powershell", ["-NoProfile", "-Command", ps], { encoding: "utf8" });
  if (r.status === 0) ok("开机自启保险：HKCU\\...\\Run 已写入");
  else warn(`Run 键写入失败：${(r.stderr || "").trim()}`);
}

function removeRunKey() {
  spawnSync("powershell", ["-NoProfile", "-Command",
    `Remove-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run' -Name '${TASK_NAME}' -ErrorAction SilentlyContinue`],
    { encoding: "utf8" });
}

// ---- Windows 策略（可选，默认不写） --------------------------------------
// 注意：HKCU\Software\Policies\Claude 一旦存在，桌面端会认为该设备是"受管设备"，
// 从而锁死在管理员配置流程里（窗口打不开），本机这种自建 3P 场景**不要**写策略。
function installPolicy() {
  if (process.env.CCD_WRITE_POLICY !== "1") {
    info("跳过 Windows 策略写入（受管设备会导致桌面端无法启动；如确需请设 CCD_WRITE_POLICY=1）");
    return;
  }
  const ps = `$k='HKCU:\\Software\\Policies\\Claude';` +
    `if (!(Test-Path $k)) { New-Item -Path $k -Force | Out-Null };` +
    `New-ItemProperty -Path $k -Name 'desktopHome' -Value 'no-vm' -PropertyType String -Force | Out-Null;` +
    `New-ItemProperty -Path $k -Name 'chatTabEnabled' -Value 'true' -PropertyType String -Force | Out-Null;` +
    `New-ItemProperty -Path $k -Name 'coworkTabEnabled' -Value 'false' -PropertyType String -Force | Out-Null;`;
  const r = spawnSync("powershell", ["-NoProfile", "-Command", ps], { encoding: "utf8" });
  if (r.status === 0) ok("Windows 策略已写入：HKCU\\Software\\Policies\\Claude（desktopHome=no-vm）");
  else warn(`Windows 策略写入失败：${(r.stderr || "").trim()}`);
}

function removePolicy() {
  spawnSync("powershell", ["-NoProfile", "-Command",
    "Remove-ItemProperty -Path 'HKCU:\\Software\\Policies\\Claude' -Name 'desktopHome','chatTabEnabled','coworkTabEnabled' -ErrorAction SilentlyContinue"],
    { encoding: "utf8" });
}

// ---- 桌面端重启 -----------------------------------------------------------
function restartApp() {
  try {
    spawnSync("taskkill", ["/IM", "claude.exe", "/F"], { encoding: "utf8" });
  } catch { /* ignore */ }
  setTimeout(() => {
    const exe = path.join(process.env.LOCALAPPDATA || "", "AnthropicClaude", "claude.exe");
    if (fs.existsSync(exe)) {
      spawn(exe, [], { detached: true, stdio: "ignore" }).unref();
      info("Claude Desktop 已重新启动");
    }
  }, 2500);
}

// ---- 安装 -----------------------------------------------------------------
async function doInstall() {
  ensureBackup();
  fs.mkdirSync(GATEWAY_DIR, { recursive: true });
  for (const f of ["ccd_config.mjs", "ccd_upstream.mjs", "ccd_gateway.mjs", "ccd_profile.mjs"]) {
    fs.copyFileSync(path.join(__dirname, "gateway", f), path.join(GATEWAY_DIR, f));
  }
  // 自安装：之后可以直接用安装目录里那份 setup.mjs 做 --status / --key
  const selfSrc = path.join(__dirname, "setup.mjs");
  const selfDst = path.join(INSTALL_DIR, "setup.mjs");
  if (path.resolve(selfSrc) !== path.resolve(selfDst)) fs.copyFileSync(selfSrc, selfDst);
  ok(`网关代码：${GATEWAY_DIR}`);
  writeLauncher();
  if (!currentKey()) askKey();

  stopGateway();
  profile.applyProfile(CONFIG_FILE);
  installPolicy();
  installTask();
  installRunKey();
  await startGateway();
  restartApp();
  info("");
  ok("安装完成，Claude Desktop 已重启并进入 3P（Gateway）模式。");
  info(`状态：node ${path.join(INSTALL_DIR, "setup.mjs")} --status    日志：${cfg.logPath()}`);
  warn(`还原：node ${path.join(INSTALL_DIR, "setup.mjs")} --restore`);
}

function askKey() {
  const input = ask("请输入 OpenCode Go API Key（https://opencode.ai/zen 获取）: ");
  if (!input) die("未输入 API Key。");
  writeConfig((c) => { c.upstream_api_key = input.trim(); });
}

async function doKey(newKey) {
  let key = newKey;
  if (!key) {
    info(`当前 Key：${maskKey(currentKey())}`);
    key = ask("输入新的 OpenCode Go API Key（回车保持不变）: ");
  }
  if (!key) die("未提供新的 API Key。");
  writeConfig((c) => { c.upstream_api_key = key.trim(); });
  stopGateway();
  await startGateway();
  profile.applyProfile(CONFIG_FILE);
  ok(`已更新 Key：${maskKey(key.trim())}`);
}

async function doTune(args) {
  writeConfig((c) => {
    if (args.port) c.port = Number(args.port);
    if (args.effort) {
      c.default_effort = args.effort;
      c.routes.forEach((r) => { r.effort = args.effort; });
    }
    if (args.localTitle) c.local_title_synthesis = args.localTitle === "on";
  });
  stopGateway();
  await startGateway();
  profile.applyProfile(CONFIG_FILE);
  ok("设置已更新");
}

async function doStatus() {
  cfg.loadConfig(CONFIG_FILE);
  await gateway.status(CONFIG_FILE);
  profile.status();
  info(taskExists() ? `计划任务：${TASK_NAME}（已创建）` : `计划任务：未创建`);
}

function doRestore() {
  removeTask();
  removeRunKey();
  removePolicy();
  stopGateway();
  const manifest = fs.existsSync(MANIFEST) ? fs.readFileSync(MANIFEST, "utf8") : "";
  const original = (manifest.match(/claude3p_existed="(.*)"/) || [])[1];
  profile.restore(BACKUP_DIR, original);
  info(`网关配置与备份保留在 ${INSTALL_DIR}`);
  restartApp();
  ok("还原完成，Claude Desktop 已重启。");
}

// 简易同步输入（Windows 控制台）
function readLineSync() {
  const buf = Buffer.alloc(1024);
  const bytes = fs.readSync(0, buf, 0, 1024, null);
  return buf.slice(0, bytes).toString("utf8").trim();
}

function ask(msg) {
  process.stdout.write(msg);
  const answer = readLineSync();
  process.stdout.write("\n");
  return answer;
}

async function main() {
  const argv = process.argv.slice(2);
  // 支持 "--status" / "status" / "--key <key>" / "key <key>" 等写法
  const raw = argv[0] || "install";
  const cmd = raw.replace(/^--/, "").toLowerCase();
  const rest = argv.slice(1);
  switch (cmd) {
    case "install": return doInstall();
    case "key": return doKey(rest[0]);
    case "port": return doTune({ port: rest[0] });
    case "effort": return doTune({ effort: rest[0] });
    case "local-title": return doTune({ localTitle: rest[0] });
    case "status": return doStatus();
    case "restore": return doRestore();
    case "help":
    case "h": {
      console.log(`用法：node setup.mjs [install|key [key]|port <端口>|effort <级别>|local-title on|off|status|restore]`);
      return;
    }
    default:
      console.log(`用法：node setup.mjs [install|key [key]|port <端口>|effort <级别>|local-title on|off|status|restore]`);
      process.exit(1);
  }
}

main();
