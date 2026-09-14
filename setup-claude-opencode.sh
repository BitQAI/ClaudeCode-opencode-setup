#!/usr/bin/env bash
#
# Claude Code × OpenCode Go 一键配置脚本
#   macOS / Linux / Windows(Git Bash) 通用
#
# 作用：让 Claude Code 通过 https://opencode.ai/zen/go 使用 Go 订阅模型
#       （默认主模型 deepseek-v4.1-flash，原生多模态）。
#
# 用法：
#   bash setup-claude-opencode.sh                 # 安装/更新（无 Key 时交互输入）
#   bash setup-claude-opencode.sh --key           # 查看/修改 API Key（交互）
#   bash setup-claude-opencode.sh --key sk-xxxx   # 直接设置 API Key
#   bash setup-claude-opencode.sh --status        # 查看当前配置与连通性
#   bash setup-claude-opencode.sh --restore       # 还原到安装前状态
#   bash setup-claude-opencode.sh --update        # 拉取远程最新脚本并重新部署
#
set -euo pipefail

SCRIPT_VERSION="1.0.0"
REPO_SLUG="BitQAI/ClaudeCode-opencode-setup"
REPO_RAW="https://raw.githubusercontent.com/${REPO_SLUG}/main"

# ---------- 可调参数 ----------
BASE_URL_DEFAULT="https://opencode.ai/zen/go"
MODEL_MAIN_DEFAULT="deepseek-v4.1-flash"
MODEL_OPUS_DEFAULT="deepseek-v4-pro"
MODEL_SONNET_DEFAULT="deepseek-v4.1-flash"
MODEL_HAIKU_DEFAULT="deepseek-v4-flash"
# 默认档（Sonnet）= deepseek-v4.1-flash，推理强度拉满
EFFORT_DEFAULT="max"

# ---------- 输出 ----------
if [ -t 1 ]; then
  C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
  C_RED=""; C_GREEN=""; C_YELLOW=""; C_DIM=""; C_OFF=""
fi
info() { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$C_GREEN" "$C_OFF" "$*"; }
warn() { printf '%s!%s %s\n' "$C_YELLOW" "$C_OFF" "$*"; }
die()  { printf '%s✗%s %s\n' "$C_RED" "$C_OFF" "$*" >&2; exit 1; }

usage() {
  cat <<EOF
Claude Code × OpenCode Go 配置脚本 v${SCRIPT_VERSION}

  (无参数)            安装或更新配置（备份现有设置，写入 opencode Go 端点）
  --key [sk-xxxx]     查看 / 设置 API Key（省略值时交互输入）
  --effort <level>    设置推理强度（low|medium|high|xhigh|max，默认 max）
  --status            显示当前配置、脱敏 Key 与端点连通性
  --restore           还原到安装前状态（含移除 API Key 授权记录）
  --update            从 GitHub 拉取最新脚本并重新部署
  -h, --help          显示本帮助
EOF
}

# ---------- 环境探测 ----------
detect_platform() {
  IS_WINDOWS=0
  case "$(uname -s 2>/dev/null || echo unknown)" in
    MINGW*|MSYS*|CYGWIN*) IS_WINDOWS=1 ;;
  esac
}

detect_python() {
  local cand out
  for cand in python3 python; do
    out="$(command -v "$cand" 2>/dev/null || true)"
    [ -n "$out" ] || continue
    # 跳过 Windows 应用商店的 python 占位符
    if "$out" -c 'import sys; sys.exit(0)' >/dev/null 2>&1; then
      PYTHON="$out"
      return 0
    fi
  done
  die "未找到可用的 Python 3（脚本需要它来读写 JSON）。请安装 Python 3.8+ 后重试。"
}

resolve_paths() {
  if [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then
    CLAUDE_DIR="$CLAUDE_CONFIG_DIR"
    STATE_FILE="$CLAUDE_DIR/.claude.json"
  else
    CLAUDE_DIR="$HOME/.claude"
    STATE_FILE="$HOME/.claude.json"
  fi
  INSTALL_DIR="$CLAUDE_DIR/claude-opencode-setup"
  BACKUP_DIR="$CLAUDE_DIR/backup-claude-opencode"
  SETTINGS_FILE="$CLAUDE_DIR/settings.json"
  ENV_FILE="$INSTALL_DIR/.env"
  MANIFEST_FILE="$BACKUP_DIR/manifest.txt"
}

mask_key() {
  local key="$1"
  if [ "${#key}" -le 12 ]; then printf '***'; else printf '%s...%s' "${key:0:7}" "${key: -4}"; fi
}

# ---------- API Key ----------
load_key() {
  if [ -f "$ENV_FILE" ]; then
    # shellcheck disable=SC1090
    . "$ENV_FILE" 2>/dev/null || true
  fi
  API_KEY="${OPENCODE_API_KEY:-}"
}

save_key() {
  mkdir -p "$INSTALL_DIR"
  ( umask 077; printf 'OPENCODE_API_KEY=%s\n' "$1" > "$ENV_FILE" )
  chmod 600 "$ENV_FILE" 2>/dev/null || true
}

ask_key() {
  local input
  printf '请输入 OpenCode Go API Key（https://opencode.ai/zen 获取，sk- 开头）: ' >&2
  read -r input || true
  [ -n "$input" ] || die "未输入 API Key。"
  case "$input" in
    sk-*) ;;
    *) warn "该 Key 不以 sk- 开头，请确认是否是 OpenCode Go 的 Key。" ;;
  esac
  API_KEY="$input"
}

verify_endpoint() {
  local key="$1" model="$2" sid http body tmp
  sid="$(uuidgen 2>/dev/null || echo "sid-$$-$RANDOM")"
  tmp="$(mktemp 2>/dev/null || echo "/tmp/opencode-verify-$$.json")"
  http="$(curl -sS -m 90 -o "$tmp" -w '%{http_code}' \
    "$BASE_URL/v1/messages" \
    -H "x-api-key: $key" \
    -H "anthropic-version: 2023-06-01" \
    -H "content-type: application/json" \
    -H "x-claude-code-session-id: $sid" \
    -d "{\"model\":\"$model\",\"max_tokens\":16,\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}]}" \
    2>/dev/null || true)"
  body="$(head -c 300 "$tmp" 2>/dev/null || true)"
  rm -f "$tmp" 2>/dev/null || true
  if [ "$http" = "200" ]; then
    return 0
  fi
  warn "连通性检查失败（HTTP ${http:-000}）：$body"
  return 1
}

# ---------- 配置写入 ----------
apply_settings() {
  mkdir -p "$CLAUDE_DIR"
  "$PYTHON" - "$SETTINGS_FILE" "$API_KEY" "$BASE_URL" \
      "$MODEL_MAIN" "$MODEL_OPUS" "$MODEL_SONNET" "$MODEL_HAIKU" "$EFFORT" <<'PY'
import json
import os
import sys

path, key, base, main, opus, sonnet, haiku, effort = sys.argv[1:9]
data = {}
if os.path.exists(path):
    raw = open(path, encoding="utf-8").read().strip()
    if raw:
        data = json.loads(raw)

env = data.setdefault("env", {})
env.update({
    "ANTHROPIC_BASE_URL": base,
    "ANTHROPIC_API_KEY": key,
    "ANTHROPIC_MODEL": main,
    "ANTHROPIC_DEFAULT_OPUS_MODEL": opus,
    "ANTHROPIC_DEFAULT_SONNET_MODEL": sonnet,
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": haiku,
    "CLAUDE_CODE_SUBAGENT_MODEL": main,
    "CLAUDE_CODE_EFFORT_LEVEL": effort,
})
# opencode Go 的 /v1/messages 只认 x-api-key，Bearer 会返回 401 Missing API key
env.pop("ANTHROPIC_AUTH_TOKEN", None)

with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
os.chmod(path, 0o600)
PY
}

approve_api_key() {
  [ -f "$STATE_FILE" ] || printf '{}\n' > "$STATE_FILE"
  "$PYTHON" - "$STATE_FILE" "$API_KEY" <<'PY'
import json
import os
import sys

path, key = sys.argv[1], sys.argv[2]
raw = open(path, encoding="utf-8").read().strip()
data = json.loads(raw) if raw else {}

suffix = key[-20:]
resp = data.setdefault("customApiKeyResponses", {})
approved = [x for x in resp.get("approved", []) if x != suffix]
approved.append(suffix)
resp["approved"] = approved
resp["rejected"] = [x for x in resp.get("rejected", []) if x != suffix]

with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
os.chmod(path, 0o600)
PY
}

write_manifest() {
  mkdir -p "$BACKUP_DIR"
  {
    printf 'installed_at="%s"\n' "$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo unknown)"
    printf 'script_version="%s"\n' "$SCRIPT_VERSION"
    printf 'settings_existed="%s"\n' "$SETTINGS_EXISTED"
    printf 'state_existed="%s"\n' "$STATE_EXISTED"
    printf 'base_url="%s"\n' "$BASE_URL"
  } > "$MANIFEST_FILE"
}

# ---------- 动作 ----------
backup_once() {
  mkdir -p "$BACKUP_DIR"
  SETTINGS_EXISTED="no"; STATE_EXISTED="no"
  if [ -f "$SETTINGS_FILE" ]; then
    cp "$SETTINGS_FILE" "$BACKUP_DIR/settings.json"
    SETTINGS_EXISTED="yes"
  fi
  if [ -f "$STATE_FILE" ]; then
    cp "$STATE_FILE" "$BACKUP_DIR/claude.json"
    STATE_EXISTED="yes"
  fi
  write_manifest
  ok "已备份到 $BACKUP_DIR"
}

# 只保留"首次安装前"那一份备份，重复运行不会覆盖它，保证 --restore 能回到最初状态
ensure_backup() {
  if [ -f "$MANIFEST_FILE" ]; then
    info "已存在安装前备份（$BACKUP_DIR），保留不覆盖"
    return 0
  fi
  backup_once
}

do_install() {
  mkdir -p "$CLAUDE_DIR"
  ensure_backup
  load_key
  [ -n "$API_KEY" ] || ask_key

  info "正在校验 API Key（$BASE_URL）..."
  verify_endpoint "$API_KEY" "$MODEL_MAIN" && ok "API Key 校验通过" || warn "校验未通过，仍会写入配置，可稍后用 --key 重试"

  save_key "$API_KEY"
  apply_settings
  approve_api_key
  install_self

  ok "settings.json 已更新：$SETTINGS_FILE"
  ok "API Key 授权已记录：$STATE_FILE"
  info ""
  info "完成。请【重启 Claude Code】后使用。当前模型映射："
  info "  ANTHROPIC_MODEL              = $MODEL_MAIN"
  info "  ANTHROPIC_DEFAULT_OPUS_MODEL  = $MODEL_OPUS"
  info "  ANTHROPIC_DEFAULT_SONNET_MODEL= $MODEL_SONNET"
  info "  ANTHROPIC_DEFAULT_HAIKU_MODEL = $MODEL_HAIKU"
  info "  CLAUDE_CODE_EFFORT_LEVEL     = $EFFORT"
}

install_self() {
  local src="${BASH_SOURCE[0]:-}" dest
  mkdir -p "$INSTALL_DIR"
  dest="$INSTALL_DIR/$SCRIPT_NAME"
  if [ -n "$src" ] && [ -f "$src" ] && [ -s "$src" ]; then
    if [ "$(cd "$(dirname "$src")" && pwd)" = "$(cd "$INSTALL_DIR" && pwd)" ]; then
      return 0
    fi
    cp "$src" "$dest" && chmod +x "$dest" 2>/dev/null
    return 0
  fi
  # 通过 `bash <(curl ...)` / 管道运行时，本地没有可复制的脚本文件，从 GitHub 取一份
  if curl -fsSL "$REPO_RAW/$SCRIPT_NAME" -o "$dest" 2>/dev/null; then
    chmod +x "$dest" 2>/dev/null || true
    info "已从 GitHub 自安装到 $dest"
  else
    warn "无法自安装脚本到 $INSTALL_DIR（不影响本次配置，可稍后手动复制）"
  fi
}

do_key() {
  load_key
  # 首次写入前先留一份可还原的备份；已安装过则保留最初那份
  ensure_backup
  local new_key="${1:-}"
  if [ -n "$new_key" ]; then
    API_KEY="$new_key"
  else
    [ -n "$API_KEY" ] && info "当前 Key：$(mask_key "$API_KEY")"
    printf '输入新的 API Key（直接回车保持不变）: ' >&2
    read -r new_key || true
    [ -n "$new_key" ] && API_KEY="$new_key" || [ -n "$API_KEY" ] || die "尚未配置 API Key。"
  fi
  verify_endpoint "$API_KEY" "$MODEL_MAIN" && ok "API Key 校验通过" || warn "校验未通过，请确认 Key 是否为 OpenCode Go"
  save_key "$API_KEY"
  apply_settings
  approve_api_key
  install_self
  ok "已更新 Key：$(mask_key "$API_KEY")（重启 Claude Code 生效）"
}

do_status() {
  load_key
  info "配置文件：$SETTINGS_FILE"
  if [ -f "$SETTINGS_FILE" ]; then
    "$PYTHON" - "$SETTINGS_FILE" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
env = data.get("env", {})
for k in ("ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
          "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
          "CLAUDE_CODE_SUBAGENT_MODEL", "CLAUDE_CODE_EFFORT_LEVEL"):
    print(f"  {k:32s}= {env.get(k, '(未设置)')}")
api_key = env.get("ANTHROPIC_API_KEY", "")
print(f"  {'ANTHROPIC_API_KEY':32s}= {api_key[:7]}...{api_key[-4:]}" if api_key else "  ANTHROPIC_API_KEY                = (未设置)")
print(f"  {'ANTHROPIC_AUTH_TOKEN':32s}= {env.get('ANTHROPIC_AUTH_TOKEN', '(未设置)')}")
PY
  else
    warn "settings.json 不存在"
  fi
  if [ -n "$API_KEY" ]; then
    info "Key 文件：$ENV_FILE（$(mask_key "$API_KEY")）"
    info "连通性检查中..."
    verify_endpoint "$API_KEY" "$MODEL_MAIN" && ok "$MODEL_MAIN 调用正常" || warn "调用失败"
  else
    warn "尚未配置 API Key，运行：bash $SCRIPT_NAME --key"
  fi
}

do_effort() {
  local level="${1:-}"
  if [ -n "$level" ]; then
    EFFORT="$level"
  else
    printf '输入推理强度（low|medium|high|xhigh|max，默认 max）: ' >&2
    read -r level || true
    [ -n "$level" ] && EFFORT="$level"
  fi
  case "$EFFORT" in
    low|medium|high|xhigh|max) ;;
    *) die "无效的推理强度：$EFFORT（可选 low|medium|high|xhigh|max）" ;;
  esac
  load_key
  [ -n "$API_KEY" ] || die "尚未配置 API Key，请先运行 --key。"
  ensure_backup
  save_key "$API_KEY"
  apply_settings
  ok "已设置推理强度：$EFFORT（重启 Claude Code 生效）"
}

do_restore() {
  [ -f "$MANIFEST_FILE" ] || die "找不到备份清单 $MANIFEST_FILE，无需还原。"
  # shellcheck disable=SC1090
  . "$MANIFEST_FILE"
  load_key
  if [ "${settings_existed:-no}" = "yes" ] && [ -f "$BACKUP_DIR/settings.json" ]; then
    cp "$BACKUP_DIR/settings.json" "$SETTINGS_FILE"
    ok "已还原 settings.json"
  else
    rm -f "$SETTINGS_FILE"
    ok "已删除脚本创建的 settings.json"
  fi
  if [ "${state_existed:-no}" = "yes" ] && [ -f "$BACKUP_DIR/claude.json" ]; then
    cp "$BACKUP_DIR/claude.json" "$STATE_FILE"
    ok "已还原 API Key 授权记录"
  elif [ -n "$API_KEY" ] && [ -f "$STATE_FILE" ]; then
    cleanup_api_key_approval
    ok "已移除 API Key 授权记录"
  fi
  rm -f "$ENV_FILE"
  ok "已删除本脚本保存的 API Key"
  info "备份仍保留在 $BACKUP_DIR（如需彻底清理可手动删除）"
  info "请【重启 Claude Code】使还原生效。"
}

cleanup_api_key_approval() {
  "$PYTHON" - "$STATE_FILE" "$API_KEY" <<'PY'
import json
import os
import sys

path, key = sys.argv[1], sys.argv[2]
if not os.path.exists(path):
    raise SystemExit(0)
raw = open(path, encoding="utf-8").read().strip()
data = json.loads(raw) if raw else {}
suffix = key[-20:]
resp = data.get("customApiKeyResponses")
if resp:
    resp["approved"] = [x for x in resp.get("approved", []) if x != suffix]
    resp["rejected"] = [x for x in resp.get("rejected", []) if x != suffix]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.chmod(path, 0o600)
PY
}

do_update() {
  local script_dir="${BASH_SOURCE[0]:-}"
  script_dir="$(cd "$(dirname "$script_dir")" && pwd)"
  if [ -d "$script_dir/.git" ]; then
    info "检测到 git 仓库，正在拉取最新脚本..."
    git -C "$script_dir" pull --ff-only || warn "git pull 失败，改用下载方式"
  fi
  if [ ! -d "$script_dir/.git" ] || [ -n "${FORCE_DOWNLOAD:-}" ]; then
    local tmp
    tmp="$(mktemp 2>/dev/null || echo "/tmp/$SCRIPT_NAME.$$")"
    info "从 GitHub 下载最新脚本..."
    curl -fsSL "$REPO_RAW/$SCRIPT_NAME" -o "$tmp" || die "下载失败：$REPO_RAW/$SCRIPT_NAME"
    install_self_from "$tmp"
    info "已更新 $INSTALL_DIR/$SCRIPT_NAME"
  fi
  info "重新执行安装流程..."
  do_install
}

install_self_from() {
  mkdir -p "$INSTALL_DIR"
  cp "$1" "$INSTALL_DIR/$SCRIPT_NAME"
  chmod +x "$INSTALL_DIR/$SCRIPT_NAME" 2>/dev/null || true
}

# ---------- 入口 ----------
main() {
  SCRIPT_NAME="setup-claude-opencode.sh"
  if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"
  fi
  detect_platform
  detect_python
  resolve_paths
  BASE_URL="${OPENCODE_BASE_URL:-$BASE_URL_DEFAULT}"
  MODEL_MAIN="$MODEL_MAIN_DEFAULT"
  MODEL_OPUS="$MODEL_OPUS_DEFAULT"
  MODEL_SONNET="$MODEL_SONNET_DEFAULT"
  MODEL_HAIKU="$MODEL_HAIKU_DEFAULT"
  EFFORT="${OPENCODE_EFFORT:-$EFFORT_DEFAULT}"

  case "${1:-}" in
    ""|--install)  do_install ;;
    --key)         do_key "${2:-}" ;;
    --effort)      do_effort "${2:-}" ;;
    --status)      do_status ;;
    --restore)     do_restore ;;
    --update)      do_update ;;
    -h|--help)     usage ;;
    *)             usage; exit 1 ;;
  esac
}

main "$@"
