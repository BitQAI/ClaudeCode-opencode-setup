"""Configuration and path handling for the Claude Desktop 3P gateway."""

from __future__ import annotations

import json
import os
import pathlib
import sys

STATE_DIRNAME = ".claude-desktop-opencode"
CONFIG_NAME = "config.json"
PID_NAME = "gateway.pid"
LOG_NAME = "gateway.log"

DEFAULT_ROUTES = [
    {
        "name": "claude-sonnet-4-5",
        "label": "DeepSeek V4.1 Flash · max",
        "model": "deepseek-v4.1-flash",
        "effort": "max",
    },
    {
        "name": "claude-opus-4-5",
        "label": "DeepSeek V4 Pro",
        "model": "deepseek-v4-pro",
        "effort": "max",
    },
    {
        "name": "claude-haiku-4-5",
        "label": "DeepSeek V4 Flash",
        "model": "deepseek-v4-flash",
        "effort": "max",
    },
]

DEFAULT_CONFIG = {
    "listen": "127.0.0.1",
    "port": 8799,
    "upstream_base_url": "https://opencode.ai/zen/go",
    "upstream_api_key": "",
    "default_effort": "max",
    "local_title_synthesis": False,
    "routes": DEFAULT_ROUTES,
}


def state_dir() -> pathlib.Path:
    return pathlib.Path.home() / STATE_DIRNAME


def config_path() -> pathlib.Path:
    return state_dir() / CONFIG_NAME


def pid_path() -> pathlib.Path:
    return state_dir() / PID_NAME


def log_path() -> pathlib.Path:
    return state_dir() / LOG_NAME


def load_config(path: pathlib.Path | None = None) -> dict:
    path = pathlib.Path(path) if path else config_path()
    raw = path.read_text(encoding="utf-8") if path.exists() else "{}"
    data = json.loads(raw or "{}")
    return normalize(data)


def normalize(data: dict) -> dict:
    config = dict(DEFAULT_CONFIG)
    config.update({k: v for k, v in data.items() if k in DEFAULT_CONFIG})
    routes = config.get("routes") or DEFAULT_ROUTES
    config["routes"] = [normalize_route(route) for route in routes if route.get("name")]
    if not config["routes"]:
        raise ValueError("config.routes 不能为空")
    return config


def normalize_route(route: dict) -> dict:
    return {
        "name": str(route["name"]).strip(),
        "label": str(route.get("label") or route["name"]),
        "model": str(route.get("model") or route["name"]),
        "effort": str(route.get("effort") or DEFAULT_CONFIG["default_effort"]),
    }


def write_config(config: dict, path: pathlib.Path | None = None) -> pathlib.Path:
    path = pathlib.Path(path) if path else config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = normalize(config)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def routes_by_name(config: dict) -> dict:
    return {route["name"]: route for route in config["routes"]}


def mask(secret: str) -> str:
    if not secret:
        return "(未设置)"
    if len(secret) <= 12:
        return "***"
    return f"{secret[:7]}...{secret[-4:]}"


def main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="ccd_config", description="网关配置读写")
    parser.add_argument("command", choices=["write", "show"])
    parser.add_argument("--config", default=None)
    parser.add_argument("--key", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--effort", default=None)
    parser.add_argument("--local-title", default=None)
    parser.add_argument("--require-key", action="store_true", help="show 时若无 key 则以非 0 退出")
    args = parser.parse_args(argv)

    path = pathlib.Path(args.config) if args.config else config_path()
    config = load_config(path)
    if args.command == "show":
        if args.require_key and not config["upstream_api_key"]:
            print("尚未配置 OpenCode Go API Key", file=sys.stderr)
            return 1
        print(f"配置：{path}")
        print(f"  监听         = {config['listen']}:{config['port']}")
        print(f"  上游         = {config['upstream_base_url']}")
        print(f"  Key          = {mask(config['upstream_api_key'])}")
        print(f"  标题本地应答 = {'on' if config['local_title_synthesis'] else 'off'}")
        for route in config["routes"]:
            print(f"  route {route['name']:<20} -> {route['model']} (effort={route['effort']})")
        return 0

    if args.key is not None:
        config["upstream_api_key"] = args.key
    if args.port is not None:
        config["port"] = args.port
    if args.effort is not None:
        config["default_effort"] = args.effort
        for route in config["routes"]:
            route["effort"] = args.effort
    if args.local_title is not None:
        config["local_title_synthesis"] = args.local_title == "on"
    write_config(config, path)
    print(f"已写入 {path}（{mask(config['upstream_api_key'])}，端口 {config['port']}，"
          f"effort={config['default_effort']}，标题本地应答={'on' if config['local_title_synthesis'] else 'off'}）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
