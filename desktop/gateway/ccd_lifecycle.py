"""Process lifecycle for the gateway: pidfile, start/stop/status/logs."""

from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import sys
import time
import json
import http.client

import ccd_config

ENTRY = pathlib.Path(__file__).resolve().parent / "ccd_gateway.py"
LABEL = "com.bitqai.ccd-gateway"


def agent_plist() -> pathlib.Path:
    return pathlib.Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"


def agent_domain() -> str:
    return f"gui/{os.getuid()}/{LABEL}"


def agent_loaded() -> bool:
    result = subprocess.run(["launchctl", "print", agent_domain()],
                            capture_output=True, text=True)
    return result.returncode == 0


def reload_agent() -> int:
    """Restart the LaunchAgent in place so it re-reads the config file."""
    if not agent_loaded():
        print("LaunchAgent 未加载，无法重载")
        return 1
    subprocess.run(["launchctl", "kickstart", "-k", agent_domain()],
                   check=False, capture_output=True)
    time.sleep(1.5)
    print("LaunchAgent 已重载")
    return 0


def read_pid() -> int | None:
    path = ccd_config.pid_path()
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def is_running(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def start(config_path: str | None = None) -> int:
    if agent_loaded():
        return reload_agent()
    if agent_plist().exists():
        loaded = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(agent_plist())],
                                capture_output=True, text=True)
        if loaded.returncode == 0:
            time.sleep(1.5)
            print("网关已启动（LaunchAgent）")
            return 0
    pid = read_pid()
    if is_running(pid):
        print(f"网关已在运行（pid={pid}）")
        return 0
    ccd_config.state_dir().mkdir(parents=True, exist_ok=True)
    log = open(ccd_config.log_path(), "ab")
    args = [sys.executable, str(ENTRY), "run"]
    if config_path:
        args += ["--config", config_path]
    proc = subprocess.Popen(args, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                            start_new_session=True, cwd=str(ENTRY.parent))
    ccd_config.pid_path().write_text(str(proc.pid), encoding="utf-8")
    time.sleep(1.0)
    if proc.poll() is not None:
        print("网关启动失败，日志末尾：")
        logs(10)
        return 1
    print(f"网关已启动（pid={proc.pid}）")
    return 0


def stop() -> int:
    if agent_loaded():
        subprocess.run(["launchctl", "bootout", agent_domain()],
                       check=False, capture_output=True)
        ccd_config.pid_path().unlink(missing_ok=True)
        print("网关已停止（LaunchAgent 已卸载）")
        return 0
    pid = read_pid()
    if not is_running(pid):
        ccd_config.pid_path().unlink(missing_ok=True)
        print("网关未在运行")
        return 0
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        if not is_running(pid):
            break
        time.sleep(0.1)
    ccd_config.pid_path().unlink(missing_ok=True)
    print(f"网关已停止（pid={pid}）")
    return 0


def probe(host: str, port: int, timeout: float = 3.0) -> tuple[bool, str]:
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("GET", "/v1/models")
        response = conn.getresponse()
        payload = json.loads(response.read() or b"{}")
        return response.status == 200, f"{response.status} / {len(payload.get('data', []))} routes"
    except OSError as exc:
        return False, f"unreachable ({exc})"
    finally:
        conn.close()


def status(config_path: str | None = None) -> int:
    config = ccd_config.load_config(config_path)
    pid = read_pid()
    agent = agent_loaded()
    running = is_running(pid) or agent
    print(f"配置：{pathlib.Path(config_path) if config_path else ccd_config.config_path()}")
    print(f"监听：{config['listen']}:{config['port']}")
    print(f"上游：{config['upstream_base_url']}")
    print(f"Key ：{'已配置' if config['upstream_api_key'] else '未配置'}")
    print(f"标题本地应答：{'开' if config['local_title_synthesis'] else '关'}")
    for route in config["routes"]:
        print(f"  route {route['name']:<22} -> {route['model']} (effort={route['effort']})")
    if not running:
        print("状态：未运行")
        return 1
    ok, detail = probe(config["listen"], config["port"])
    who = "LaunchAgent" if agent else f"pid={pid}"
    print(f"状态：运行中（{who}）自检 {'通过' if ok else '失败'}：{detail}")
    return 0 if ok else 1


def logs(lines: int = 40) -> int:
    path = ccd_config.log_path()
    if not path.exists():
        print("暂无日志")
        return 1
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in content[-lines:]:
        print(line)
    return 0
