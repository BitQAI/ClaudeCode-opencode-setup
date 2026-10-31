// 上游请求整形 + 本地标题应答（对应仓库中的 gateway/ccd_upstream.py）
import crypto from "node:crypto";

export const USER_AGENT = "claude-cli/2.1.197 (external, cli)";
const PASS_HEADERS = ["anthropic-version", "anthropic-beta", "accept"];

export function upstreamParts(baseUrl) {
  const u = new URL(baseUrl);
  if (u.protocol !== "https:") throw new Error(`上游必须使用 https：${baseUrl}`);
  return { host: u.hostname, port: u.port || 443, basePath: u.pathname.replace(/\/+$/, "") };
}

// OpenCode Go 要求每个会话带稳定的 session id：优先复用客户端带来的
export function sessionId(clientHeaders, payload) {
  for (const name of ["x-claude-code-session-id", "x-opencode-session"]) {
    const value = clientHeaders[name];
    if (value) return value;
  }
  const meta = payload.metadata;
  const raw = meta && typeof meta === "object" ? meta.user_id : null;
  if (typeof raw === "string" && raw.trim().startsWith("{")) {
    try {
      const found = JSON.parse(raw).session_id;
      if (found) return found;
    } catch { /* ignore */ }
  }
  return crypto.randomUUID();
}

export function rewrite(payload, route, options = {}) {
  payload.model = route.model;
  if (!payload.output_config || typeof payload.output_config !== "object") payload.output_config = {};
  if (options.forceEffort !== false) payload.output_config.effort = route.effort;
  // 客户端（如桌面端 Claude Code）会要求 thinking.budget_tokens=32000，
  // 导致模型思考很久、回复很慢；这里限制思考预算，提升体感速度。
  const cap = options.thinkingBudgetCap;
  if (cap && payload.thinking && typeof payload.thinking === "object") {
    const b = payload.thinking.budget_tokens;
    if (typeof b === "number" && b > cap) payload.thinking.budget_tokens = cap;
  }
  return payload;
}

export function upstreamHeaders(clientHeaders, config, session) {
  const headers = {
    "content-type": "application/json",
    "x-api-key": config.upstream_api_key,
    "x-opencode-session": session,
    "user-agent": USER_AGENT,
    "content-length": undefined,
  };
  for (const name of PASS_HEADERS) {
    const value = clientHeaders[name];
    if (value) headers[name] = value;
  }
  return headers;
}

// 会话标题生成请求的特征：无 tools + output_config.format 是只含 title 的 json_schema
export function isTitleRequest(payload) {
  if (payload.tools && payload.tools.length) return false;
  const fmt = (payload.output_config || {}).format;
  if (!fmt || typeof fmt !== "object" || fmt.type !== "json_schema") return false;
  const props = (fmt.schema || {}).properties || {};
  return Object.keys(props).length === 1 && "title" in props;
}

export function deriveTitle(payload) {
  let text = "";
  for (const message of payload.messages || []) {
    if (message.role !== "user") continue;
    const content = message.content;
    if (typeof content === "string") text = content;
    else if (Array.isArray(content)) text = content.filter((c) => c && typeof c === "object").map((c) => c.text || "").join(" ");
    if (text) break;
  }
  if (text.includes("<session>") && text.includes("</session>")) {
    text = text.split("<session>")[1].split("</session>")[0];
  }
  const line = text.trim().split("\n").map((s) => s.trim()).find(Boolean) || "New session";
  return line.replace(/\s+/g, " ").slice(0, 24) || "New session";
}

export function titleEvents(model, title) {
  const text = JSON.stringify({ title });
  return [
    ["message_start", { type: "message_start", message: {
      id: `local-${crypto.randomUUID()}`, type: "message", role: "assistant",
      model, content: [], stop_reason: null, stop_sequence: null,
      usage: { input_tokens: 0, output_tokens: 0 } } }],
    ["content_block_start", { type: "content_block_start", index: 0, content_block: { type: "text", text: "" } }],
    ["content_block_delta", { type: "content_block_delta", index: 0, delta: { type: "text_delta", text } }],
    ["content_block_stop", { type: "content_block_stop", index: 0 }],
    ["message_delta", { type: "message_delta", delta: { stop_reason: "end_turn", stop_sequence: null }, usage: { output_tokens: 0 } }],
    ["message_stop", { type: "message_stop" }],
  ];
}

export function titleMessage(model, title) {
  return {
    id: `local-${crypto.randomUUID()}`, type: "message", role: "assistant", model,
    content: [{ type: "text", text: JSON.stringify({ title }) }],
    stop_reason: "end_turn", stop_sequence: null,
    usage: { input_tokens: 0, output_tokens: 0 },
  };
}
