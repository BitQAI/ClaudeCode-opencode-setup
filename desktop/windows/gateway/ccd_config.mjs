// 配置与路径处理（Windows 适配版，对应仓库中的 gateway/ccd_config.py）
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const STATE_DIRNAME = ".claude-desktop-opencode";
export const CONFIG_NAME = "config.json";
export const PID_NAME = "gateway.pid";
export const LOG_NAME = "gateway.log";

export const DEFAULT_ROUTES = [
  { name: "claude-sonnet-4-5", label: "DeepSeek V4.1 Flash · max", model: "deepseek-v4.1-flash", effort: "max" },
  { name: "claude-opus-4-5", label: "DeepSeek V4 Pro", model: "deepseek-v4-pro", effort: "max" },
  { name: "claude-haiku-4-5", label: "DeepSeek V4 Flash", model: "deepseek-v4-flash", effort: "max" },
];

export const DEFAULT_CONFIG = {
  listen: "127.0.0.1",
  port: 8799,
  upstream_base_url: "https://opencode.ai/zen/go",
  upstream_api_key: "",
  default_effort: "max",
  local_title_synthesis: false,
  upstream_connect_timeout: 30,
  upstream_read_timeout: 300,
  upstream_retries: 2,
  upstream_retry_backoff: 0.6,
  force_effort: true,
  thinking_budget_cap: 32000,
  // 上游模型真实上下文上限（token）：用于把上游空壳 400 翻译成 prompt_too_long
  upstream_context_limit: 1000000,
  routes: DEFAULT_ROUTES,
};

export function homeDir() {
  return process.env.USERPROFILE || os.homedir();
}

export function stateDir() {
  return path.join(homeDir(), STATE_DIRNAME);
}

export function configPath() {
  return path.join(stateDir(), CONFIG_NAME);
}

export function pidPath() {
  return path.join(stateDir(), PID_NAME);
}

export function logPath() {
  return path.join(stateDir(), LOG_NAME);
}

export function normalizeRoute(route) {
  return {
    name: String(route.name).trim(),
    label: String(route.label || route.name),
    model: String(route.model || route.name),
    effort: String(route.effort || DEFAULT_CONFIG.default_effort),
  };
}

export function normalize(data) {
  const config = { ...DEFAULT_CONFIG };
  for (const [k, v] of Object.entries(data || {})) {
    if (k in DEFAULT_CONFIG) config[k] = v;
  }
  const routes = (config.routes || DEFAULT_ROUTES).map(normalizeRoute).filter((r) => r.name);
  if (!routes.length) throw new Error("config.routes 不能为空");
  config.routes = routes;
  return config;
}

export function loadConfig(file) {
  const p = file ? path.resolve(file) : configPath();
  let raw = "{}";
  if (fs.existsSync(p)) raw = fs.readFileSync(p, "utf8").trim() || "{}";
  return normalize(JSON.parse(raw));
}

export function writeConfig(config, file) {
  const p = file ? path.resolve(file) : configPath();
  fs.mkdirSync(path.dirname(p), { recursive: true });
  const payload = normalize(config);
  fs.writeFileSync(p, JSON.stringify(payload, null, 2) + "\n", "utf8");
  try {
    fs.chmodSync(p, 0o600);
  } catch { /* Windows 上 chmod 只能设只读位，忽略 */ }
  return p;
}

export function routesByName(config) {
  const out = {};
  for (const r of expandedRoutes(config)) out[r.name] = r;
  return out;
}

// Claude Code 认的 1M 上下文别名后缀：请求发出前 CLI 会去掉它并附加 context-1m beta，
// 但模型窗口会从 200K 抬到 1e6，自动压缩才允许设到接近 1M。
export const LONG_CONTEXT_SUFFIX = "[1m]";
export const LONG_CONTEXT_LABEL = " · 1M";

// 为每条路由派生 `[1m]` 别名（桌面端选择器里的 1M 档），幂等：已存在的别名不重复追加
export function withLongContextVariants(routes) {
  const names = new Set(routes.map((r) => r.name));
  const expanded = [...routes];
  for (const route of routes) {
    if (route.name.endsWith(LONG_CONTEXT_SUFFIX)) continue;
    const alias = `${route.name}${LONG_CONTEXT_SUFFIX}`;
    if (names.has(alias)) continue;
    expanded.push({ ...route, name: alias, label: `${route.label}${LONG_CONTEXT_LABEL}` });
  }
  return expanded;
}

export function expandedRoutes(config) {
  return withLongContextVariants(config.routes);
}

export function mask(secret) {
  if (!secret) return "(未设置)";
  if (secret.length <= 12) return "***";
  return `${secret.slice(0, 7)}...${secret.slice(-4)}`;
}
