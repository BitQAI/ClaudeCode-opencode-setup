"""Upstream request shaping and the optional local title response."""

from __future__ import annotations

import http.client
import json
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


def rewrite(payload: dict, route: dict) -> dict:
    payload["model"] = route["model"]
    output_config = payload.setdefault("output_config", {})
    if isinstance(output_config, dict):
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


def open_upstream(config: dict, path: str):
    host, base_path = upstream_parts(config["upstream_base_url"])
    return http.client.HTTPSConnection(host, timeout=600), base_path + path


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
