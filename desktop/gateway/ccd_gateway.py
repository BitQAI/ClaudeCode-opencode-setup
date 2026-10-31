#!/usr/bin/env python3
"""Local gateway that lets Claude Desktop's 3P mode talk to OpenCode Go.

Claude Desktop only accepts Anthropic-shaped model ids, and OpenCode Go only serves
its own model ids. This gateway sits on 127.0.0.1 between them: it advertises
Anthropic-shaped routes, rewrites the model (and reasoning effort) and forwards to
the upstream with the right credentials.
"""

from __future__ import annotations

import argparse
import http.server
import json
import sys
import time
import urllib.parse

import ccd_config
import ccd_lifecycle
import ccd_routes
import ccd_upstream

HOP_BY_HOP = {
    "host", "content-length", "connection", "keep-alive", "transfer-encoding",
    "proxy-connection", "te", "trailer", "upgrade", "accept-encoding",
}
# 由网关自己决定分帧与身份标识：上游的 content-length / server / date 一律丢弃，
# 否则非流式响应会出现"既无长度也无 chunked"的歧义，客户端只能干等连接关闭。
DROP_HEADERS = HOP_BY_HOP | {"content-length", "server", "date"}
CHUNK = 2048


def append_log(message: str) -> None:
    path = ccd_config.log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")


class Gateway(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    config: dict = {}
    table: ccd_routes.RouteTable | None = None

    def log_message(self, fmt, *args):
        return

    # ---- helpers -------------------------------------------------------
    def routes(self) -> list[dict]:
        """当前可用的路由表（必要时重扫）。"""
        if self.table is None:
            self.table = ccd_routes.build_table(self.config)
        if not (self.config.get("free_scan") or {}).get("enabled", True):
            return self.config["routes"]
        return self.table.ensure_fresh()

    def route_for(self, requested: str) -> dict | None:
        """按请求里的模型名找路由。

        Desktop 会把 `claude-opus-4-5` 发成 `claude-opus-4-5-20251101`，
        所以除了精确匹配，还要用规范化后的名字再匹配一次；匹配不上就报未知模型，
        不能悄悄回退到第一条路由（那会用错模型）。
        """
        rows = self.routes()
        for route in rows:
            if route["name"] == requested:
                return route
        target = ccd_routes.canonical(requested)
        for route in rows:
            if ccd_routes.canonical(route["name"]) == target:
                return route
        return None

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status: int, kind: str, message: str) -> None:
        self.send_json(status, {"type": "error", "error": {"type": kind, "message": message}})

    def read_payload(self) -> dict | None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self.send_error_json(400, "invalid_request_error", "request body is not valid JSON")
            return None

    # ---- routes --------------------------------------------------------
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path.rstrip("/").endswith("/v1/models"):
            data = self.models_payload()
            append_log("GET /v1/models -> " + (", ".join(r["id"] for r in data) or "(empty)"))
            self.send_json(200, {"data": data, "has_more": False,
                                 "first_id": data[0]["id"] if data else None,
                                 "last_id": data[-1]["id"] if data else None})
            return
        self.send_error_json(404, "not_found_error", path)

    def models_payload(self) -> list[dict]:
        """Anthropic 风格的 /v1/models 数据，Desktop discovery 直接消费它。

        字段和 app.asar 里 Oen() 的解析一致：
        - id：路由名，必须能通过 Desktop 的 Mo() 校验；
        - display_name：picker 显示的标签（除非撞上发布目录的规范名）；
        - supports_1m / max_input_tokens：让 Desktop 生成 1M 变体；
        - description：picker 里显示的说明。
        """
        payload = []
        for route in self.routes():
            entry = {
                "type": "model",
                "id": route["name"],
                "display_name": route.get("label") or route["name"],
                "description": f"{route['model']} · OpenCode Go",
                "created_at": "2026-01-01T00:00:00Z",
            }
            if route.get("supports1m"):
                entry["supports_1m"] = True
                entry["max_input_tokens"] = int(self.config.get("context_window_tokens", 1000000))
            payload.append(entry)
        return payload

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not path.startswith("/v1/messages"):
            self.send_error_json(404, "not_found_error", path)
            return
        payload = self.read_payload()
        if payload is None:
            return
        if self.config["local_count_tokens"] and path.rstrip("/").endswith("/count_tokens"):
            self.send_count_tokens(payload)
            return
        requested = str(payload.get("model") or "")
        route = self.route_for(requested)
        if route is None:
            append_log(f"WARN unknown route {requested!r}")
            self.send_error_json(400, "invalid_request_error",
                                 f"unknown model {requested!r}")
            return
        payload = ccd_upstream.rewrite(payload, route)
        restored = ccd_upstream.ensure_server_tools(payload)
        if restored:
            append_log(f"FIX 补回 tools 定义 {restored}（{requested} -> {route['model']}）")
        if self.config["local_title_synthesis"] and ccd_upstream.is_title_request(payload):
            self.send_local_title(route, payload)
            return
        self.forward(path, route, requested, payload)

    # ---- count_tokens（本地估算，上游不提供） ---------------------------
    def send_count_tokens(self, payload: dict) -> None:
        tokens = ccd_upstream.estimate_input_tokens(payload)
        append_log(f"POST /v1/messages/count_tokens {payload.get('model')!r} -> "
                   f"local estimate {tokens} tokens")
        self.send_json(200, {"input_tokens": tokens})

    # ---- upstream ------------------------------------------------------
    def send_upstream(self, path: str, body: bytes, headers: dict):
        conn, target = ccd_upstream.open_upstream(
            self.config, path, self.config["upstream_connect_timeout"])
        try:
            conn.request("POST", target, body=body, headers=headers)
            ccd_upstream.set_read_timeout(conn, self.config["upstream_read_timeout"])
            return conn, conn.getresponse()
        except OSError:
            conn.close()
            raise

    def forward(self, path: str, route: dict, requested: str, payload: dict) -> None:
        started = time.time()
        session = ccd_upstream.session_id(self.headers, payload)
        headers = ccd_upstream.upstream_headers(self.headers, self.config, session)
        body = json.dumps(payload, ensure_ascii=False).encode()
        attempts = max(1, int(self.config["upstream_retries"]) + 1)
        conn = None
        response = None
        last_error: OSError | None = None
        for attempt in range(1, attempts + 1):
            attempt_started = time.time()
            try:
                conn, response = self.send_upstream(path, body, headers)
                break
            except OSError as exc:
                last_error = exc
                elapsed = int((time.time() - attempt_started) * 1000)
                append_log(f"WARN upstream attempt {attempt}/{attempts} failed after {elapsed}ms: "
                           f"{type(exc).__name__}: {exc}")
                if attempt < attempts:
                    time.sleep(self.config["upstream_retry_backoff"] * attempt)
        if response is None:
            append_log(f"ERROR upstream unreachable after {attempts} attempt(s): {last_error}")
            self.send_error_json(502, "api_error", f"upstream unreachable: {last_error}")
            return
        # 上游不接受显式关闭 thinking，带 thinking={"type":"disabled"} 的请求必然 400。
        # 客户端无法自行规避，只在确实被拒后去掉 thinking 重试一次，不影响本来能通过的请求。
        if response.status == 400 and ccd_upstream.thinking_disabled(payload):
            response.close()
            conn.close()
            retried = {k: v for k, v in payload.items() if k != "thinking"}
            body = json.dumps(retried, ensure_ascii=False).encode()
            append_log(f"FIX thinking=disabled 被上游拒绝，去掉 thinking 重试 "
                       f"({requested} -> {route['model']})")
            try:
                conn, response = self.send_upstream(path, body, headers)
            except OSError as exc:
                append_log(f"ERROR upstream retry failed: {type(exc).__name__}: {exc}")
                self.send_error_json(502, "api_error", f"upstream unreachable: {exc}")
                return
        try:
            streaming = "text/event-stream" in (response.getheader("content-type") or "")
            if response.status >= 400 and not streaming:
                # 错误响应体是排查上游拒绝原因的唯一线索，原样记一小段再透传回客户端
                raw = response.read()
                translated = ccd_upstream.prompt_too_long_error(
                    response.status, raw, payload, self.config)
                append_log(f"ERROR upstream {response.status} {requested} -> {route['model']} "
                           f"({len(body)}B req): {raw[:600].decode('utf-8', 'replace')}")
                if translated is not None:
                    append_log(f"FIX 上游 400 无可读原因，按上下文超限改写 "
                               f"({requested} -> {route['model']}, {len(body)}B req)")
                if self.config.get("debug_log_request_body"):
                    # 仅排查用：会把会话正文写进日志，用完请关掉
                    append_log(f"DEBUG req {requested}: {body[:4000].decode('utf-8', 'replace')}")
                self.send_response(response.status)
                self.send_header("content-type", "application/json" if translated is not None
                                 else (response.getheader("content-type") or "application/json"))
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw if translated is None else translated)
            else:
                self.relay(response, streaming)
            elapsed = int((time.time() - started) * 1000)
            append_log(f"POST {path} {requested} -> {route['model']} "
                       f"effort={route['effort']} status={response.status} {elapsed}ms")
        except (BrokenPipeError, ConnectionResetError) as exc:
            append_log(f"WARN client disconnected while relaying {requested}: {type(exc).__name__}")
        finally:
            response.close()
            conn.close()

    def relay(self, response, streaming: bool) -> None:
        headers = [(k, v) for k, v in response.getheaders() if k.lower() not in DROP_HEADERS]
        if not streaming:
            data = response.read()
            self.send_response(response.status)
            for key, value in headers:
                self.send_header(key, value)
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(response.status)
        for key, value in headers:
            self.send_header(key, value)
        self.send_header("transfer-encoding", "chunked")
        self.end_headers()
        while True:
            chunk = response.read(CHUNK)
            if not chunk:
                break
            self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
        self.wfile.write(b"0\r\n\r\n")

    # ---- optional local title -----------------------------------------
    def send_local_title(self, route: dict, payload: dict) -> None:
        title = ccd_upstream.derive_title(payload)
        append_log(f"POST /v1/messages {route['name']} -> local title {title!r}")
        if payload.get("stream"):
            body = b"".join(
                f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()
                for name, data in ccd_upstream.title_events(route["model"], title))
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_json(200, ccd_upstream.title_message(route["model"], title))


def serve(config: dict) -> None:
    Gateway.config = config
    Gateway.table = ccd_routes.build_table(config)
    server = http.server.ThreadingHTTPServer((config["listen"], config["port"]), Gateway)
    append_log(f"gateway listening on {config['listen']}:{config['port']} "
               f"upstream={config['upstream_base_url']} routes={len(config['routes'])} "
               f"local_title_synthesis={config['local_title_synthesis']}")
    server.serve_forever()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ccd_gateway", description="Claude Desktop 3P gateway")
    parser.add_argument("command", nargs="?", default="status",
                        choices=["run", "start", "stop", "status", "logs"])
    parser.add_argument("--config", default=None, help="配置文件路径")
    parser.add_argument("-n", "--lines", type=int, default=40, help="logs 显示行数")
    args = parser.parse_args(argv)

    if args.command == "run":
        serve(ccd_config.load_config(args.config))
        return 0
    if args.command == "start":
        return ccd_lifecycle.start(args.config)
    if args.command == "stop":
        return ccd_lifecycle.stop()
    if args.command == "logs":
        return ccd_lifecycle.logs(args.lines)
    return ccd_lifecycle.status(args.config)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
