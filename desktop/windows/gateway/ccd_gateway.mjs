// Claude Desktop 3P 本地网关（对应仓库中的 gateway/ccd_gateway.py，Windows/Node 实现）
//
// 桌面端只认 Anthropic 风格模型名，OpenCode Go 只认自己的模型名，
// 因此这里在 127.0.0.1 上做一层翻译：改写 model + 强制 effort + 注入会话头。
import http from "node:http";
import https from "node:https";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";

import * as cfg from "./ccd_config.mjs";
import * as upstream from "./ccd_upstream.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const HOP_BY_HOP = new Set([
  "host", "content-length", "connection", "keep-alive", "transfer-encoding",
  "proxy-connection", "te", "trailer", "upgrade", "accept-encoding",
]);

export function appendLog(message) {
  try {
    const p = cfg.logPath();
    fs.mkdirSync(path.dirname(p), { recursive: true });
    const now = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    const ts = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
    fs.appendFileSync(p, `${ts} ${message}\n`, "utf8");
  } catch { /* 日志写失败不能影响服务 */ }
}

function sendJson(res, status, payload) {
  const body = Buffer.from(JSON.stringify(payload), "utf8");
  res.writeHead(status, { "content-type": "application/json", "content-length": body.length });
  res.end(body);
}

function sendError(res, status, kind, message) {
  sendJson(res, status, { type: "error", error: { type: kind, message } });
}

// Anthropic 风格模型名（claude-*）走路由表；其它名字（opencode 的模型名）原样透传
const UPSTREAM_HINT = /^(deepseek|glm|kimi|qwen|minimax|gpt|gro(k|4)|muse|omen|hy\d|beam|step|longcat|mimo|gemma|luna|spark|contributor)/i;

function routeFor(config, requested) {
  const routes = cfg.routesByName(config);
  const route = routes[requested];
  if (route) return route;
  if (requested && !/^claude-/i.test(requested) && UPSTREAM_HINT.test(requested)) {
    // 终端 Claude Code 直接使用上游模型名时，原样透传
    return { name: requested, label: requested, model: requested, effort: config.default_effort };
  }
  const fallback = config.routes[0];
  appendLog(`WARN unknown route '${requested}' -> fallback ${fallback.name}`);
  return fallback;
}

function readBody(req) {
  // Node 的 http 服务端已经完成 chunked 解码，直接收集 data 事件即可
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}

async function handle(req, res, config) {
  const url = new URL(req.url, `http://${req.headers.host || "127.0.0.1"}`);
  const pathname = url.pathname;

  if (req.method === "GET" && pathname.replace(/\/+$/, "").endsWith("/v1/models")) {
    const data = cfg.expandedRoutes(config).map((r) => ({
      type: "model", id: r.name, display_name: r.label, created_at: "2026-01-01T00:00:00Z",
    }));
    appendLog(`GET /v1/models -> ${data.length} routes`);
    sendJson(res, 200, { data, has_more: false, first_id: data[0].id, last_id: data[data.length - 1].id });
    return;
  }

  if (req.method === "POST" && pathname.startsWith("/v1/messages")) {
    const raw = await readBody(req);
    let payload;
    try {
      payload = raw.length ? JSON.parse(raw.toString("utf8")) : {};
    } catch {
      sendError(res, 400, "invalid_request_error", "request body is not valid JSON");
      return;
    }
    const requested = String(payload.model || "");
    const route = routeFor(config, requested);
    appendLog(`recv POST ${pathname} model=${requested || "(无)"} bytes=${raw.length}`);

    // count_tokens：上游（OpenCode Go）不实现该端点，本地估算后直接返回，避免 CLI 卡住
    if (pathname.startsWith("/v1/messages/count_tokens")) {
      payload.model = route.model;
      const tokens = estimateTokens(payload);
      appendLog(`POST /v1/messages/count_tokens ${requested} -> local estimate ${tokens} tokens`);
      sendJson(res, 200, { input_tokens: tokens });
      return;
    }

    payload = upstream.rewrite(payload, route, {
      forceEffort: config.force_effort !== false,
      thinkingBudgetCap: config.thinking_budget_cap,
    });

    if (config.local_title_synthesis && upstream.isTitleRequest(payload)) {
      sendLocalTitle(res, route, payload);
      return;
    }
    await forward(req, res, config, route, requested, payload);
    return;
  }

  sendError(res, 404, "not_found_error", pathname);
}

function sendLocalTitle(res, route, payload) {
  const title = upstream.deriveTitle(payload);
  appendLog(`POST /v1/messages ${route.name} -> local title '${title}'`);
  if (payload.stream) {
    const body = Buffer.from(
      upstream.titleEvents(route.model, title)
        .map(([name, data]) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`)
        .join(""), "utf8");
    res.writeHead(200, { "content-type": "text/event-stream", "content-length": body.length });
    res.end(body);
    return;
  }
  sendJson(res, 200, upstream.titleMessage(route.model, title));
}

// 粗略估算 token 数（英文约 4 字符/token，中文约 1.5 字符/token）
function estimateTokens(payload) {
  const text = JSON.stringify({
    system: payload.system || "",
    messages: payload.messages || [],
    tools: payload.tools || [],
  });
  let cjk = 0;
  for (const ch of text) if (/[\u4e00-\u9fff\u3040-\u30ff]/.test(ch)) cjk += 1;
  const rest = text.length - cjk;
  return Math.max(1, Math.ceil(rest / 4 + cjk / 1.5));
}

// 上游错误体最多记多少字符（只用于日志/改写，不缓存整个响应）
const ERROR_BODY_LOG_CHARS = 600;

// 上游（OpenCode Go）在 prompt 超出模型上下文时只回一个空壳 400（如 {"model":"deepseek-v4.1-flash"}），
// Claude Code 认不出这是 prompt_too_long，就不会做反应式压缩，界面上只显示
// "Invalid request / The request couldn't be completed."。
// 这里用本地 token 估算把它翻译成 Anthropic 风格、可被客户端识别的错误。
function respondUpstreamError(res, config, payload, status, raw) {
  const limit = Number(config.upstream_context_limit) || 1000000;
  const estimated = estimateTokens(payload);
  const opaque = raw.length <= 200 || /^\{"model"/.test(raw);
  if ((status === 400 || status === 413) && opaque && estimated > limit * 0.95) {
    const message = `prompt is too long: ${estimated} tokens > ${limit} maximum`;
    appendLog(`rewrite upstream ${status} -> prompt_too_long (estimated ${estimated} tokens, limit ${limit})`);
    sendError(res, 400, "invalid_request_error", message);
    return;
  }
  const detail = raw ? raw.slice(0, 300) : "empty body";
  sendError(res, status, "api_error", `upstream ${status}: ${detail}`);
}

function forward(req, res, config, route, requested, payload, passthrough = false) {
  return new Promise((resolve) => {
    const started = Date.now();
    const session = upstream.sessionId(req.headers, payload);
    const body = Buffer.from(JSON.stringify(payload), "utf8");
    const headers = upstream.upstreamHeaders(req.headers, config, session);
    headers["content-length"] = String(body.length);

    let { host, port, basePath } = upstream.upstreamParts(config.upstream_base_url);
    const attempts = Math.max(1, Number(config.upstream_retries) + 1);
    let attempt = 0;

    const send = () => {
      attempt += 1;
      const attemptStarted = Date.now();
      const options = {
        host, port, method: "POST", path: basePath + req.url.replace(/^\/v1/, "/v1"),
        headers, rejectUnauthorized: true,
      };
      const upReq = https.request(options, (upRes) => {
        const status = upRes.statusCode || 502;
        const label = `POST ${req.url.split("?")[0]} ${requested} -> ${route.model} effort=${passthrough ? "-" : route.effort}`;

        // 错误响应体通常很小：先收下来，进日志，再决定是否改写成可读错误
        if (status >= 400) {
          const chunks = [];
          let size = 0;
          upRes.on("data", (c) => {
            if (size < ERROR_BODY_LOG_CHARS) { chunks.push(c); size += c.length; }
          });
          upRes.on("error", () => resolve());
          upRes.on("end", () => {
            const raw = Buffer.concat(chunks).toString("utf8").replace(/\s+/g, " ").trim().slice(0, ERROR_BODY_LOG_CHARS);
            appendLog(`${label} status=${status} ${Date.now() - started}ms upstream_body=${JSON.stringify(raw)}`);
            if (!res.headersSent) respondUpstreamError(res, config, payload, status, raw);
            resolve();
          });
          return;
        }

        const outHeaders = {};
        for (const [k, v] of Object.entries(upRes.headers)) {
          if (!HOP_BY_HOP.has(k.toLowerCase())) outHeaders[k] = v;
        }
        res.writeHead(status, outHeaders);
        upRes.pipe(res);
        upRes.on("end", () => {
          appendLog(`${label} status=${status} ${Date.now() - started}ms`);
          resolve();
        });
        upRes.on("error", () => resolve());
      });
      upReq.setTimeout(Number(config.upstream_read_timeout) * 1000, () => {
        upReq.destroy(new Error("upstream read timeout"));
      });
      upReq.on("error", (err) => {
        const elapsed = Date.now() - attemptStarted;
        if (!res.headersSent) {
          if (attempt < attempts) {
            appendLog(`WARN upstream attempt ${attempt}/${attempts} failed after ${elapsed}ms: ${err.message}`);
            setTimeout(send, Number(config.upstream_retry_backoff) * 1000 * attempt);
            return;
          }
          appendLog(`ERROR upstream unreachable after ${attempts} attempt(s): ${err.message}`);
          sendError(res, 502, "api_error", `upstream unreachable: ${err.message}`);
        }
        resolve();
      });
      upReq.write(body);
      upReq.end();
    };
    send();
  });
}

export function serve(config) {
  const server = http.createServer((req, res) => {
    handle(req, res, config).catch((err) => {
      appendLog(`ERROR ${err && err.stack ? err.stack : err}`);
      if (!res.headersSent) sendError(res, 500, "api_error", String(err));
    });
  });
  server.keepAliveTimeout = 0;
  server.headersTimeout = 0;
  server.requestTimeout = 0;
  server.on("clientError", (err, socket) => socket.destroy());
  server.listen(config.port, config.listen, () => {
    try { fs.writeFileSync(cfg.pidPath(), String(process.pid), "utf8"); } catch { /* ignore */ }
    appendLog(`gateway listening on ${config.listen}:${config.port} upstream=${config.upstream_base_url} routes=${config.routes.length} local_title_synthesis=${config.local_title_synthesis}`);
  });
  return server;
}

// ---- 守护进程（ supervise ）：不依赖任何控制台窗口，挂了自动拉起 -------------
export function supervise(configFile) {
  const args = [path.join(__dirname, "ccd_gateway.mjs"), "run"];
  if (configFile) args.push("--config", configFile);
  const config = cfg.loadConfig(configFile);
  let child = null;
  let stopping = false;

  const spawnChild = () => {
    child = spawn(process.execPath, args, {
      detached: false,
      stdio: ["ignore", "inherit", "inherit"],
      cwd: __dirname,
      windowsHide: true,
    });
    child.on("exit", (code, signal) => {
      if (stopping) return;
      appendLog(`WARN gateway child exited (code=${code} signal=${signal}); restarting in 3s`);
      setTimeout(spawnChild, 3000);
    });
  };

  // 若已有其它实例在监听（如计划任务与 Run 键各起了一个），就不要再抢端口
  const ensureChild = async () => {
    if (stopping) return;
    const detail = await probe(config);
    if (String(detail).startsWith("200")) {
      if (!child) appendLog("supervisor: 已有网关实例在监听，本实例不重复启动");
      setTimeout(ensureChild, 10000);
      return;
    }
    if (!child) spawnChild();
    setTimeout(ensureChild, 5000);
  };

  const shutdown = () => {
    stopping = true;
    if (child) { try { child.kill(); } catch { /* ignore */ } }
    setTimeout(() => process.exit(0), 500);
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
  process.on("exit", () => { if (child) { try { child.kill(); } catch { /* ignore */ } } });

  appendLog(`supervisor started (pid=${process.pid}) -> ${args.join(" ")}`);
  ensureChild();
  // 保持主进程存活
  setInterval(() => {}, 1 << 30);
}

// ---- 生命周期（pidfile 方式，替代 macOS 的 LaunchAgent） --------------------
function readPid() {
  try {
    if (!fs.existsSync(cfg.pidPath())) return null;
    const v = parseInt(fs.readFileSync(cfg.pidPath(), "utf8").trim(), 10);
    return Number.isFinite(v) ? v : null;
  } catch { return null; }
}

function isRunning(pid) {
  if (!pid || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch { return false; }
}

export function start(configFile) {
  const pid = readPid();
  if (isRunning(pid)) {
    console.log(`网关已在运行（pid=${pid}）`);
    return 0;
  }
  fs.mkdirSync(cfg.stateDir(), { recursive: true });
  const log = fs.openSync(cfg.logPath(), "a");
  const args = [path.join(__dirname, "ccd_gateway.mjs"), "run"];
  if (configFile) args.push("--config", configFile);
  const detached = spawn(process.execPath, args, { detached: true, stdio: ["ignore", log, log], cwd: __dirname });
  detached.unref();
  fs.writeFileSync(cfg.pidPath(), String(detached.pid), "utf8");
  // 等自检通过再返回
  const deadline = Date.now() + 10000;
  const wait = () => new Promise((r) => setTimeout(r, 300));
  (async () => {
    while (Date.now() < deadline) {
      await wait();
      if (await probe(cfg.loadConfig(configFile))) {
        console.log(`网关已启动（pid=${detached.pid}）`);
        return;
      }
    }
    console.log(`网关启动失败（pid=${detached.pid}），日志末尾：`);
    logs(10);
  })();
  return 0;
}

export function stop() {
  const pid = readPid();
  if (!isRunning(pid)) {
    try { fs.unlinkSync(cfg.pidPath()); } catch { /* ignore */ }
    console.log("网关未在运行");
    return 0;
  }
  try { process.kill(pid, "SIGTERM"); } catch { /* ignore */ }
  setTimeout(() => {
    try { fs.unlinkSync(cfg.pidPath()); } catch { /* ignore */ }
    console.log(`网关已停止（pid=${pid}）`);
  }, 300);
  return 0;
}

export function probe(config) {
  return new Promise((resolve) => {
    const req = http.request({
      host: config.listen, port: config.port, path: "/v1/models", method: "GET", timeout: 3000,
    }, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => {
        let n = 0;
        try { n = (JSON.parse(Buffer.concat(chunks).toString("utf8")).data || []).length; } catch { /* ignore */ }
        resolve(res.statusCode === 200 ? `200 / ${n} routes` : `${res.statusCode}`);
      });
    });
    req.on("error", (err) => resolve(`unreachable (${err.message})`));
    req.on("timeout", () => { req.destroy(); resolve("unreachable (timeout)"); });
    req.end();
  });
}

export async function status(configFile) {
  const config = cfg.loadConfig(configFile);
  const pid = readPid();
  const running = isRunning(pid);
  console.log(`配置：${configFile ? path.resolve(configFile) : cfg.configPath()}`);
  console.log(`监听：${config.listen}:${config.port}`);
  console.log(`上游：${config.upstream_base_url}`);
  console.log(`Key ：${config.upstream_api_key ? "已配置" : "未配置"}`);
  console.log(`标题本地应答：${config.local_title_synthesis ? "开" : "关"}`);
  for (const r of cfg.expandedRoutes(config)) console.log(`  route ${r.name.padEnd(22)} -> ${r.model} (effort=${r.effort})`);
  if (!running) {
    console.log("状态：未运行");
    return 1;
  }
  const detail = await probe(config);
  console.log(`状态：运行中（pid=${pid}）自检 ${detail}`);
  return 0;
}

export function logs(lines = 40) {
  const p = cfg.logPath();
  if (!fs.existsSync(p)) {
    console.log("暂无日志");
    return 1;
  }
  const content = fs.readFileSync(p, "utf8").split(/\r?\n/).filter(Boolean);
  for (const line of content.slice(-lines)) console.log(line);
  return 0;
}

// ---- CLI -------------------------------------------------------------------
if (process.argv[1] && path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url))) {
  const argv = process.argv.slice(2);
  const parser = new Set(["run", "start", "stop", "status", "logs", "supervise"]);
  const command = argv[0] && parser.has(argv[0]) ? argv[0] : "status";
  const configIdx = argv.indexOf("--config");
  const configFile = configIdx >= 0 ? argv[configIdx + 1] : null;
  const nIdx = argv.indexOf("-n");
  const lines = nIdx >= 0 ? parseInt(argv[nIdx + 1], 10) : 40;

  if (command === "run") {
    serve(cfg.loadConfig(configFile));
  } else if (command === "supervise") {
    supervise(configFile);
  } else if (command === "start") {
    start(configFile);
  } else if (command === "stop") {
    stop();
  } else if (command === "logs") {
    logs(lines);
  } else {
    status(configFile).then((code) => process.exit(code));
  }
}
