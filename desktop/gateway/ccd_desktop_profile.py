"""Claude Desktop 3P profile management (the Claude-3p config library)."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import sys
import uuid

import ccd_config

PROFILE_NAME = "OpenCode Go (DeepSeek)"


def claude3p_dir() -> pathlib.Path:
    return pathlib.Path.home() / "Library/Application Support/Claude-3p"


def library_dir() -> pathlib.Path:
    return claude3p_dir() / "configLibrary"


def live_config_path() -> pathlib.Path:
    return claude3p_dir() / "claude_desktop_config.json"


def profile_id() -> str:
    path = ccd_config.state_dir() / "profile-id"
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = str(uuid.uuid4())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return value


def read_json(path: pathlib.Path) -> dict:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    return json.loads(raw) if raw else {}


def write_private_json(path: pathlib.Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def profile_payload(config: dict) -> dict:
    return {
        "inferenceProvider": "gateway",
        "inferenceGatewayBaseUrl": f"http://{config['listen']}:{config['port']}",
        "inferenceGatewayApiKey": "local-gateway-key",
        "inferenceGatewayAuthScheme": "x-api-key",
        "inferenceModels": [{"name": r["name"], "labelOverride": r["label"]}
                            for r in config["routes"]],
    }


def apply_profile(config_path: str | None = None) -> int:
    config = ccd_config.load_config(config_path)
    pid = profile_id()
    meta_path = library_dir() / "_meta.json"
    meta = read_json(meta_path)
    entries = [e for e in meta.get("entries", [])
               if isinstance(e, dict) and e.get("id") != pid]
    entries.append({"id": pid, "name": PROFILE_NAME})
    meta["entries"] = entries
    meta["appliedId"] = pid

    write_private_json(library_dir() / f"{pid}.json", profile_payload(config))
    write_private_json(meta_path, meta)

    live = read_json(live_config_path())
    live["deploymentMode"] = "3p"
    write_private_json(live_config_path(), live)
    print(f"已写入 3P profile：{library_dir() / f'{pid}.json'}（appliedId={pid}，deploymentMode=3p）")
    return 0


def status() -> int:
    meta_path = library_dir() / "_meta.json"
    meta = read_json(meta_path)
    live = read_json(live_config_path())
    pid = meta.get("appliedId")
    entry = next((e for e in meta.get("entries", []) if e.get("id") == pid), {})
    profile = read_json(library_dir() / f"{pid}.json") if pid else {}
    print(f"Claude-3p 目录：{claude3p_dir()}")
    print(f"  appliedId    = {pid or '(无)'}")
    print(f"  名称         = {entry.get('name', '(未知)')}")
    print(f"  provider     = {profile.get('inferenceProvider', '(未配置)')}")
    print(f"  gateway      = {profile.get('inferenceGatewayBaseUrl', '(未配置)')}")
    print(f"  models       = {[m.get('name') for m in profile.get('inferenceModels', [])]}")
    print(f"  deployment   = {live.get('deploymentMode', '(未设置)')}")
    return 0


def restore(backup_dir: str, original_existed: str | None = None) -> int:
    source = pathlib.Path(backup_dir) / "Claude-3p"
    target = claude3p_dir()
    # 只处理配置项；Claude-3p 下的 vm_bundles / Cache 等运行数据一律不动
    backed_up = [name for name in ("configLibrary", "claude_desktop_config.json", "config.json")
                 if (source / name).exists()]
    if not backed_up:
        if original_existed == "no":
            # 安装前不存在 Claude-3p 配置：移除脚本创建的配置项
            for name in ("configLibrary",):
                stale = target / name
                if stale.exists():
                    shutil.rmtree(stale)
            live = target / "claude_desktop_config.json"
            if live.exists():
                live.unlink()
            print(f"安装前无 Claude-3p 配置，已移除脚本创建的配置项（保留 {target} 下的其它数据）")
            return 0
        print(f"备份中无配置项（{source}），保留现有 {target} 不做改动")
        return 0
    target.mkdir(parents=True, exist_ok=True)
    for name in backed_up:
        src = source / name
        dst = target / name
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    print(f"已还原配置项 {backed_up} 到 {target}（来源 {source}）")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ccd_desktop_profile", description="Claude Desktop 3P profile 管理")
    parser.add_argument("command", choices=["apply", "status", "restore"])
    parser.add_argument("--config", default=None)
    parser.add_argument("--backup-dir", default=None)
    parser.add_argument("--original-existed", default=None, choices=["yes", "no"])
    args = parser.parse_args(argv)
    if args.command == "apply":
        return apply_profile(args.config)
    if args.command == "status":
        return status()
    if not args.backup_dir:
        parser.error("restore 需要 --backup-dir")
    return restore(args.backup_dir, args.original_existed)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
