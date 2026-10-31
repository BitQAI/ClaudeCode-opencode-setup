// Claude Desktop 3P profile 管理（对应仓库中的 gateway/ccd_desktop_profile.py，Windows 适配）
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";

import * as cfg from "./ccd_config.mjs";

export const PROFILE_NAME = "OpenCode Go (DeepSeek)";

export function appDataDir() {
  return process.env.APPDATA || path.join(cfg.homeDir(), "AppData", "Roaming");
}

export function localAppDataDir() {
  return process.env.LOCALAPPDATA || path.join(cfg.homeDir(), "AppData", "Local");
}

// 3P 模式下的用户数据目录
// macOS  : ~/Library/Application Support/Claude-3p
// Windows: %LOCALAPPDATA%\Claude-3p
export function claude3pDir() {
  return process.env.CCD_CLAUDE3P_DIR || path.join(localAppDataDir(), "Claude-3p");
}

export function libraryDir() {
  return path.join(claude3pDir(), "configLibrary");
}

export function liveConfigPath() {
  return path.join(claude3pDir(), "claude_desktop_config.json");
}

export function profileId() {
  const p = path.join(cfg.stateDir(), "profile-id");
  if (fs.existsSync(p)) {
    const v = fs.readFileSync(p, "utf8").trim();
    if (v) return v;
  }
  fs.mkdirSync(path.dirname(p), { recursive: true });
  const value = crypto.randomUUID();
  fs.writeFileSync(p, value, "utf8");
  return value;
}

export function readJson(p) {
  if (!fs.existsSync(p)) return {};
  const raw = fs.readFileSync(p, "utf8").trim();
  return raw ? JSON.parse(raw) : {};
}

export function writePrivateJson(p, payload) {
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, JSON.stringify(payload, null, 2) + "\n", "utf8");
  try { fs.chmodSync(p, 0o600); } catch { /* Windows 忽略 */ }
}

export function profilePayload(config) {
  return {
    inferenceProvider: "gateway",
    inferenceGatewayBaseUrl: `http://${config.listen}:${config.port}`,
    inferenceGatewayApiKey: "local-gateway-key",
    inferenceGatewayAuthScheme: "x-api-key",
    inferenceModels: cfg.expandedRoutes(config).map((r) => ({ name: r.name, labelOverride: r.label })),
    // Windows 上的 Cowork 依赖 .msix 安装包（本机无法获取），统一面板降级为无 VM 模式
    desktopHome: "no-vm",
    chatTabEnabled: true,
    chatAdvancedFileAnalysisEnabled: false,
    coworkTabEnabled: false,
    isClaudeCodeForDesktopEnabled: true,
  };
}

export function applyProfile(configFile) {
  const config = cfg.loadConfig(configFile);
  const pid = profileId();
  const metaPath = path.join(libraryDir(), "_meta.json");
  const meta = readJson(metaPath);
  const entries = (meta.entries || []).filter((e) => e && e.id !== pid);
  entries.push({ id: pid, name: PROFILE_NAME });
  meta.entries = entries;
  meta.appliedId = pid;

  writePrivateJson(path.join(libraryDir(), `${pid}.json`), profilePayload(config));
  writePrivateJson(metaPath, meta);

  const live = readJson(liveConfigPath());
  live.deploymentMode = "3p";
  writePrivateJson(liveConfigPath(), live);

  console.log(`已写入 3P profile：${path.join(libraryDir(), `${pid}.json`)}（appliedId=${pid}，deploymentMode=3p）`);
  return 0;
}

export function status() {
  const metaPath = path.join(libraryDir(), "_meta.json");
  const meta = readJson(metaPath);
  const live = readJson(liveConfigPath());
  const pid = meta.appliedId;
  const entry = (meta.entries || []).find((e) => e && e.id === pid) || {};
  const profile = pid ? readJson(path.join(libraryDir(), `${pid}.json`)) : {};
  console.log(`Claude-3p 目录：${claude3pDir()}`);
  console.log(`  appliedId    = ${pid || "(无)"}`);
  console.log(`  名称         = ${entry.name || "(未知)"}`);
  console.log(`  provider     = ${profile.inferenceProvider || "(未配置)"}`);
  console.log(`  gateway      = ${profile.inferenceGatewayBaseUrl || "(未配置)"}`);
  console.log(`  models       = ${JSON.stringify((profile.inferenceModels || []).map((m) => m.name))}`);
  console.log(`  deployment   = ${live.deploymentMode || "(未设置)"}`);
  return 0;
}

export function restore(backupDir, originalExisted) {
  const source = path.join(backupDir, "Claude-3p");
  const target = claude3pDir();
  const names = ["configLibrary", "claude_desktop_config.json", "config.json"].filter((n) => fs.existsSync(path.join(source, n)));
  if (!names.length) {
    if (originalExisted === "no") {
      const lib = path.join(target, "configLibrary");
      if (fs.existsSync(lib)) fs.rmSync(lib, { recursive: true, force: true });
      const live = path.join(target, "claude_desktop_config.json");
      if (fs.existsSync(live)) fs.unlinkSync(live);
      console.log(`安装前无 Claude-3p 配置，已移除脚本创建的配置项（保留 ${target} 下的其它数据）`);
      return 0;
    }
    console.log(`备份中无配置项（${source}），保留现有 ${target} 不做改动`);
    return 0;
  }
  fs.mkdirSync(target, { recursive: true });
  for (const name of names) {
    const src = path.join(source, name);
    const dst = path.join(target, name);
    fs.cpSync(src, dst, { recursive: true, force: true });
  }
  console.log(`已还原配置项 ${JSON.stringify(names)} 到 ${target}（来源 ${source}）`);
  return 0;
}
