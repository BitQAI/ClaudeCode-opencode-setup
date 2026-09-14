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
import ccd_upstream

HOP_BY_HOP = {
    "host", "content-length", "connection", "keep-alive", "transfer-encoding",
    "proxy-connection", "te", "trailer", "upgrade", "accept-encoding",
}
CHUNK = 2048


def append_log(message: str) -> None:
    path = ccd_config.log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")


class Gateway(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    config: dict = {}

    def log_message(self, fmt, *args):
        return

    # ---- helpers -------------------------------------------------------
    def route_for(self, requested: str) -> dict:
        routes = ccd_config.routes_by_name(self.config)
        route = routes.get(requested)
        if route is None:
            route = self.config["routes"][0]
            append_log(f"WARN unknown route {requested!r} -> fallback {route['name']}")
        return route

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
            data = [{"type": "model", "id": r["name"], "display_name": r["label"],
                     "created_at": "2026-01-01T00:00:00Z"} for r in self.config["routes"]]
            append_log(f"GET /v1/models -> {len(data)} routes")
            self.send_json(200, {"data": data, "has_more": False,
                                 "first_id": data[0]["id"], "last_id": data[-1]["id"]})
            return
        self.send_error_json(404, "not_found_error", path)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not path.startswith("/v1/messages"):
            self.send_error_json(404, "not_found_error", path)
            return
        payload = self.read_payload()
        if payload is None:
            return
        requested = str(payload.get("model") or "")
        route = self.route_for(requested)
        payload = ccd_upstream.rewrite(payload, route)
        if self.config["local_title_synthesis"] and ccd_upstream.is_title_request(payload):
            self.send_local_title(route, payload)
            return
        self.forward(path, route, requested, payload)

    # ---- upstream ------------------------------------------------------
    def forward(self, path: str, route: dict, requested: str, payload: dict) -> None:
        started = time.time()
        session = ccd_upstream.session_id(self.headers, payload)
        headers = ccd_upstream.upstream_headers(self.headers, self.config, session)
        body = json.dumps(payload, ensure_ascii=False).encode()
        try:
            conn, target = ccd_upstream.open_upstream(self.config, path)
            conn.request("POST", target, body=body, headers=headers)
            response = conn.getresponse()
        except OSError as exc:
            append_log(f"ERROR upstream unreachable: {exc}")
            self.send_error_json(502, "api_error", f"upstream unreachable: {exc}")
            return
        try:
            streaming = "text/event-stream" in (response.getheader("content-type") or "")
            self.relay(response, streaming)
            elapsed = int((time.time() - started) * 1000)
            append_log(f"POST {path} {requested} -> {route['model']} "
                       f"effort={route['effort']} status={response.status} {elapsed}ms")
        finally:
            conn.close()

    def relay(self, response, streaming: bool) -> None:
        self.send_response(response.status)
        for key, value in response.getheaders():
            if key.lower() not in HOP_BY_HOP:
                self.send_header(key, value)
        if streaming:
            self.send_header("transfer-encoding", "chunked")
        self.end_headers()
        if not streaming:
            self.wfile.write(response.read())
            return
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
