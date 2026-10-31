"""Upstream request shaping and the optional local title response."""

from __future__ import annotations

import http.client
import json
import re
import urllib.parse
import uuid

USER_AGENT = "claude-cli/2.1.197 (external, cli)"
PASS_HEADERS = ("anthropic-version", "anthropic-beta", "accept")


def upstream_parts(base_url: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme != "https":
        raise ValueError(f"上游必须使用 https：{base_url}")
    return parsed.netloc, parsed.path.rstrip("/")


def session_id(client_headers, payload: dict) -> str:
    """OpenCode Go needs a stable per-conversation id; reuse the client's when present."""
    for name in ("x-claude-code-session-id", "x-opencode-session"):
        value = client_headers.get(name)
        if value:
            return value
    meta = payload.get("metadata") or {}
    raw = meta.get("user_id") if isinstance(meta, dict) else None
    if isinstance(raw, str) and raw.lstrip().startswith("{"):
        try:
            found = json.loads(raw).get("session_id")
        except json.JSONDecodeError:
            found = None
        if found:
            return found
    return str(uuid.uuid4())


# 上游（opencode）对服务端工具有一条硬性约束：会话历史里一旦出现 server_tool_use /
# web_search_tool_result 块，tools 数组就必须保留对应的工具定义，否则一律返回
# invalid_request_error（实测：带 tools 200，剥离 tools 400）。客户端在续传时省略
# tools 就会踩到，这里补回去。
KNOWN_SERVER_TOOLS = {
    "web_search": {"type": "web_search_20250305", "name": "web_search", "max_uses": 5},
}


def used_server_tool_names(payload: dict) -> set:
    names = set()
    for message in payload.get("messages") or []:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "server_tool_use":
                name = block.get("name")
                if isinstance(name, str):
                    names.add(name)
    return names


def ensure_server_tools(payload: dict) -> list:
    """补回会话已用到但 tools 里缺失的服务端工具定义，返回被补上的工具名。"""
    used = used_server_tool_names(payload)
    if not used:
        return []
    tools = payload.get("tools")
    if not isinstance(tools, list):
        tools = []
    defined = {t.get("name") for t in tools if isinstance(t, dict)}
    restored = []
    for name in sorted(used - defined):
        spec = KNOWN_SERVER_TOOLS.get(name)
        if spec:
            tools.append(dict(spec))
            restored.append(name)
    payload["tools"] = tools
    return restored


def thinking_disabled(payload: dict) -> bool:
    """上游不接受显式关闭 thinking：只要请求里带 thinking={"type":"disabled"} 就必然 400。

    与是否声明 tools 无关（实测 2x2 矩阵：disabled 恒 400，enabled 恒 200）。
    客户端在部分调用路径上会带上这个字段，网关只能在上游拒绝后去掉它重试。
    """
    thinking = payload.get("thinking")
    return isinstance(thinking, dict) and thinking.get("type") == "disabled"


# 上游没有 count_tokens（/v1/messages/count_tokens 会落到 opencode 官网的 404 页面），客户端
# 只能退回 JSON.stringify({system,messages,tools}).length / 4 这种估算。该口径对拉丁文够用，
# 但把 CJK 当成 1 个字符位（实际约 0.65 token/字），会低估一半以上，于是上下文用量显示偏小。
# 这里用同一口径，只把宽字符按 1.6 个字符位折算，让 CJK 与拉丁文落在同一尺度上。
# 已知偏差：base64、十六进制串等高熵内容仍会被低估（实测约 0.4 倍），字符数无法区分。
COUNT_TOKEN_CHARS = 4
COUNT_TOKEN_FLOOR = 16
WIDE_WEIGHT = 1.6
WIDE_CHAR_RE = re.compile("[\u2e80-\U0010ffff]")
# 实测最密的内容（base64）约 1.4 字符/token，用于“是否可能已超窗口”的悲观判定。
DENSE_CHARS_PER_TOKEN = 1.4


def _request_blob(payload: dict) -> str:
    return json.dumps({
        "system": payload.get("system"),
        "messages": payload.get("messages"),
        "tools": payload.get("tools"),
    }, ensure_ascii=False)


def estimate_input_tokens(payload: dict) -> int:
    blob = _request_blob(payload)
    wide = 0 if blob.isascii() else sum(1 for _ in WIDE_CHAR_RE.finditer(blob))
    return (len(blob) + int(WIDE_WEIGHT * wide)) // COUNT_TOKEN_CHARS + COUNT_TOKEN_FLOOR


def dense_input_tokens(payload: dict) -> int:
    """悲观口径：假设内容密到 1.4 字符/token 时的大小。"""
    return len(_request_blob(payload)) // DENSE_CHARS_PER_TOKEN + COUNT_TOKEN_FLOOR


# 上游拒收请求时只回 {"model":"<id>"}，没有可读原因，客户端只能显示
# `API Error: 400 {"model":"..."}`。实测这个长相同时对应上下文超限和 messages 为空这类
# 请求体校验失败，所以不能只看签名。客户端识别超限靠消息里的 "prompt is too long"
# （Claude Code 的 Sor()），另会解析 "N tokens > M" 取具体数字，这里按同一格式改写。
PROMPT_TOO_LONG_MESSAGE = "prompt is too long: {tokens} tokens > {limit} maximum"
# 换算成 token 后仍低于窗口这个比例就当成别的 400，原样透传，不冒充超限。
PROMPT_TOO_LONG_MIN_RATIO = 0.9


def prompt_too_long_error(status: int, raw: bytes, payload: dict, config: dict):
    """上游返回无法辨识的 400，且自家估算确认已顶到窗口时，改写成客户端认识的形式。"""
    if status != 400:
        return None
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(body, dict) or set(body) != {"model"}:
        return None
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    limit = int(config["context_window_tokens"])
    tokens = estimate_input_tokens(payload)
    if dense_input_tokens(payload) < limit * PROMPT_TOO_LONG_MIN_RATIO:
        return None
    # 上游不给真实 token 数，只能报自家估算，并保证 > limit 让客户端能算出溢出量
    message = PROMPT_TOO_LONG_MESSAGE.format(tokens=max(tokens, limit + 1), limit=limit)
    return json.dumps({"type": "error",
                       "error": {"type": "invalid_request_error", "message": message}},
                      ensure_ascii=False).encode()


# Desktop 会把 effort 选项按模型给出；只有这些值是上游认识的
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


def rewrite(payload: dict, route: dict) -> dict:
    payload["model"] = route["model"]
    output_config = payload.setdefault("output_config", {})
    if not isinstance(output_config, dict):
        return payload
    requested = output_config.get("effort")
    if route.get("effort_mode") == "client" and requested in EFFORT_LEVELS:
        # 客户端能选 effort，就按它选的走（上游对 5 档都实测通过）
        output_config["effort"] = requested
    else:
        # 客户端选不了 effort，用路由配置的固定值
        output_config["effort"] = route["effort"]
    return payload


def upstream_headers(client_headers, config: dict, session: str) -> dict:
    headers = {
        "content-type": "application/json",
        "x-api-key": config["upstream_api_key"],
        "x-opencode-session": session,
        "user-agent": USER_AGENT,
    }
    for name in PASS_HEADERS:
        value = client_headers.get(name)
        if value:
            headers[name] = value
    return headers


def open_upstream(config: dict, path: str, connect_timeout: float = 30.0):
    host, base_path = upstream_parts(config["upstream_base_url"])
    return http.client.HTTPSConnection(host, timeout=connect_timeout), base_path + path


def set_read_timeout(conn, seconds: float) -> None:
    """After the TLS handshake, allow the model to think for much longer."""
    if conn.sock is not None:
        conn.sock.settimeout(seconds)


def is_title_request(payload: dict) -> bool:
    """The app's conversation-title call: no tools, a json_schema that only has 'title'."""
    if payload.get("tools"):
        return False
    fmt = (payload.get("output_config") or {}).get("format")
    if not isinstance(fmt, dict) or fmt.get("type") != "json_schema":
        return False
    props = (fmt.get("schema") or {}).get("properties") or {}
    return list(props) == ["title"]


def derive_title(payload: dict) -> str:
    text = ""
    for message in payload.get("messages") or []:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
        if text:
            break
    if "<session>" in text and "</session>" in text:
        text = text.split("<session>", 1)[1].split("</session>", 1)[0]
    line = next((part.strip() for part in text.strip().splitlines() if part.strip()), "New session")
    return " ".join(line.split())[:24] or "New session"


def title_events(model: str, title: str) -> list[tuple[str, dict]]:
    text = json.dumps({"title": title}, ensure_ascii=False)
    return [
        ("message_start", {"type": "message_start", "message": {
            "id": f"local-{uuid.uuid4()}", "type": "message", "role": "assistant",
            "model": model, "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": text}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta",
                           "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                           "usage": {"output_tokens": 0}}),
        ("message_stop", {"type": "message_stop"}),
    ]


def title_message(model: str, title: str) -> dict:
    return {
        "id": f"local-{uuid.uuid4()}", "type": "message", "role": "assistant", "model": model,
        "content": [{"type": "text", "text": json.dumps({"title": title}, ensure_ascii=False)}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }
