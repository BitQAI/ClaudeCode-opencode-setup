"""Configuration and path handling for the Claude Desktop 3P gateway."""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import time

STATE_DIRNAME = ".claude-desktop-opencode"
CONFIG_NAME = "config.json"
PID_NAME = "gateway.pid"
LOG_NAME = "gateway.log"

DEFAULT_ROUTES = [
    {
        # 固定用 claude-sonnet-4-5 做默认模型：Desktop 会记住用户选中的
        # 模型 id，改名会让已保存的选择失效。它也是列表里的第一项。
        "name": "claude-sonnet-4-5",
        "model": "deepseek-v4.1-flash",
        "effort": "max",
        "supports1m": True,
    },
]

# 免费模型扫描参数
DEFAULT_FREE_SCAN = {
    "enabled": True,
    # 扫描结果的最长有效期；到期后 /v1/models 会触发一次刷新。
    # Desktop 每次启动都会 discovery 一次，所以这个值决定"重开就重扫"。
    "max_age_seconds": 60,
    # 是否逐个真发请求探通。默认关：直接相信上游 /v1/models 的目录，
    # 快（一次请求）且不会因为探针超时误删模型；代价是目录里残留的
    # 下线/地区受限模型也会被列出来，点开才会报错。
    "probe": False,
    "list_timeout_seconds": 20,
    "probe_timeout_seconds": 25,
    "workers": 16,
    "docs_url": "https://opencode.ai/v2/docs/console/models/",
    "extra_candidates": [],
}

# 能让 Desktop 给出完整 effort 档位（low…max）的名字族。
# 只有 mythos/fable 系列匹配：发布目录里没有 mythos 条目，所以不会撞名；
# 而 Desktop 对 mythos 系列会走 C9t 分支，给出 low/medium/high/xhigh/max。
EFFORT_CAPABLE_RE = re.compile(r"^(?:claude-)?(?:fable|mythos)(?:-|$)")


def effort_capable(name: str) -> bool:
    """这个路由名在 Desktop 里能不能拿到 effort 档位。

    拿不到 effort 档位时，客户端根本不会带 output_config.effort，
    网关就只能按路由配置强制一个固定值，标签里写死才准确。
    """
    return bool(EFFORT_CAPABLE_RE.match(str(name).strip().lower()))


# 可分配给新模型的路由名池。
# 三个硬约束（详见 gateway/ccd_routes.py 顶部说明）：
#   1. Claude Code CLI 认识（否则 CLI 按 200k 上下文处理并报警告）；
#   2. CLI 不会把它改写成别的模型（claude-opus-4-0 会被改写成 claude-opus-5-5）；
#   3. 不与发布目录里的规范名撞名（否则选择器用官方名覆盖我们的显示名）。
DEFAULT_ROUTE_POOL = [
    "claude-mythos-5",
    "claude-mythos-5-1",
    "claude-mythos-preview",
    "claude-fable-5-mythos-5",
]

DEFAULT_CONFIG = {
    "listen": "127.0.0.1",
    "port": 8799,
    "upstream_base_url": "https://opencode.ai/zen/go",
    "upstream_api_key": "",
    "default_effort": "max",
    "local_title_synthesis": False,
    "local_count_tokens": True,
    "context_window_tokens": 1000000,
    "upstream_connect_timeout": 30,
    "upstream_read_timeout": 300,
    "upstream_retries": 2,
    "upstream_retry_backoff": 0.6,
    "debug_log_request_body": False,
    "routes": DEFAULT_ROUTES,
    "free_scan": DEFAULT_FREE_SCAN,
    # 无论扫描结果如何都必须出现在列表里的模型
    "pinned_models": [],
    # 可分配给新模型的路由名池
    "route_pool": DEFAULT_ROUTE_POOL,
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
    scan = dict(DEFAULT_FREE_SCAN)
    scan.update(config.get("free_scan") or {})
    config["free_scan"] = scan
    config["pinned_models"] = [str(m) for m in (config.get("pinned_models") or [])]
    config["route_pool"] = [str(n) for n in (config.get("route_pool") or DEFAULT_ROUTE_POOL)]
    return config


def normalize_route(route: dict) -> dict:
    name = str(route["name"]).strip()
    normalized = {
        "name": name,
        "model": str(route.get("model") or route["name"]),
        "effort": str(route.get("effort") or DEFAULT_CONFIG["default_effort"]),
        # 仅在显式声明时保留，交给 Claude Desktop 去开启 1M 上下文变体
        "supports1m": bool(route.get("supports1m", True)),
        # 客户端能选 effort 就听客户端的，选不了就用路由配置的固定值
        "effort_mode": "client" if effort_capable(name) else "fixed",
    }
    # 只有显式写了 label 才保留；否则交给路由表按上游模型名生成，
    # 不能回退成路由名（否则选择器里会显示 claude-mythos-7 这种假名字）。
    if route.get("label"):
        normalized["label"] = str(route["label"])
    return normalized


def write_config(config: dict, path: pathlib.Path | None = None) -> pathlib.Path:
    path = pathlib.Path(path) if path else config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = normalize(config)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def routes_by_name(config: dict) -> dict:
    return {route["name"]: route for route in config["routes"]}


def append_log(message: str) -> None:
    """把网关自己的日志写进 gateway.log。"""
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")


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
    parser.add_argument("--local-count-tokens", default=None,
                        help="on/off：本地估算 /v1/messages/count_tokens")
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
        print(f"  Token 本地估算 = {'on' if config['local_count_tokens'] else 'off'}")
        print(f"  上下文窗口   = {config['context_window_tokens']}")
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
    if args.local_count_tokens is not None:
        config["local_count_tokens"] = args.local_count_tokens == "on"
    write_config(config, path)
    print(f"已写入 {path}（{mask(config['upstream_api_key'])}，端口 {config['port']}，"
          f"effort={config['default_effort']}，标题本地应答={'on' if config['local_title_synthesis'] else 'off'}，"
          f"Token 本地估算={'on' if config['local_count_tokens'] else 'off'}）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
