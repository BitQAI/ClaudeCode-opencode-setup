#!/usr/bin/env bash
#
# Claude Desktop × OpenCode Go 一键配置（3P 本地网关模式，macOS）
#
#   bash setup-desktop-opencode.sh                  # 安装 / 更新
#   bash setup-desktop-opencode.sh --key            # 查看 / 设置 OpenCode Go API Key
#   bash setup-desktop-opencode.sh --port 8799      # 修改监听端口
#   bash setup-desktop-opencode.sh --effort max     # 修改推理强度
#   bash setup-desktop-opencode.sh --local-title on # 标题请求本地应答（省一次上游调用）
#   bash setup-desktop-opencode.sh --status         # 查看状态
#   bash setup-desktop-opencode.sh --restore        # 还原到安装前
#
set -euo pipefail

SCRIPT_VERSION="1.0.0"
REPO_SLUG="BitQAI/ClaudeCode-opencode-setup"
REPO_BRANCH="main"
REPO_SUBDIR="desktop"
REPO_TARBALL="https://github.com/${REPO_SLUG}/archive/refs/heads/${REPO_BRANCH}.tar.gz"
SCRIPT_NAME="setup-desktop-opencode.sh"
APP_DIR="/Applications/Claude.app"
LABEL="com.bitqai.ccd-gateway"

if [ -t 1 ]; then
  C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_OFF=$'\033[0m'
else
  C_RED=""; C_GREEN=""; C_YELLOW=""; C_OFF=""
fi
info() { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$C_GREEN" "$C_OFF" "$*"; }
warn() { printf '%s!%s %s\n' "$C_YELLOW" "$C_OFF" "$*"; }
die()  { printf '%s✗%s %s\n' "$C_RED" "$C_OFF" "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Claude Desktop × OpenCode Go 配置脚本

  (无参数)               安装 / 更新（备份 → 写网关配置 → 写 3P profile → 装 LaunchAgent）
  --key [sk-xxxx]        查看 / 设置 OpenCode Go API Key
  --port <端口>          修改网关监听端口（默认 8799）
  --effort <级别>        修改推理强度 low|medium|high|xhigh|max（默认 max）
  --local-title <on|off> 标题请求是否由网关本地应答（默认 off，开启可省一次上游调用）
  --status               查看网关 / profile / LaunchAgent 状态
  --restore              还原到安装前状态（含移除 LaunchAgent）
  --update               从 GitHub 拉取最新脚本并重新部署
  -h, --help             显示本帮助
EOF
}

detect_env() {
  [ "$(uname -s)" = "Darwin" ] || die "目前仅支持 macOS。"
  [ -d "$APP_DIR" ] || die "未找到 Claude Desktop（$APP_DIR）。"
  PYTHON="$(command -v python3 || true)"
  [ -n "$PYTHON" ] || die "未找到 python3，请先安装 Python 3.8+。"
  INSTALL_DIR="$HOME/.claude-desktop-opencode"
  GATEWAY_DIR="$INSTALL_DIR/gateway"
  CONFIG_FILE="$INSTALL_DIR/config.json"
  BACKUP_DIR="$INSTALL_DIR/backup"
  MANIFEST="$BACKUP_DIR/manifest.txt"
  AGENT_PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
  GATEWAY_SRC="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)/gateway"
  CLAUDE3P="$HOME/Library/Application Support/Claude-3p"
  # 假 HOME（测试/沙箱）下绝不操作真实 launchd 域，降级为 pidfile 方式
  SKIP_AGENT=0
  local real_home
  real_home="$(dscl . -read "/Users/$USER" NFSHomeDirectory 2>/dev/null | awk '{print $2}')"
  [ -n "$real_home" ] || real_home="$(eval echo "~$USER")"
  if [ -n "$real_home" ] && [ "$HOME" != "$real_home" ]; then
    SKIP_AGENT=1
    warn "HOME（$HOME）不是真实用户目录，跳过 LaunchAgent，改用 pidfile 方式"
  fi
}

mask_key() {
  local key="$1"
  if [ "${#key}" -le 12 ]; then printf '***'; else printf '%s...%s' "${key:0:7}" "${key: -4}"; fi
}

config_cli() { "$PYTHON" "$GATEWAY_DIR/ccd_config.py" "$@" --config "$CONFIG_FILE"; }
gateway_cli() { "$PYTHON" "$GATEWAY_DIR/ccd_gateway.py" "$@" --config "$CONFIG_FILE"; }
profile_cli() { "$PYTHON" "$GATEWAY_DIR/ccd_desktop_profile.py" "$@"; }

ensure_backup() {
  if [ -f "$MANIFEST" ]; then
    info "已存在安装前备份（$BACKUP_DIR），保留不覆盖"
    return 0
  fi
  mkdir -p "$BACKUP_DIR/Claude-3p"
  # 只备份配置项：Claude-3p 内还有数 GB 的 VM/Cache 数据，整体复制既慢又无必要
  for item in configLibrary claude_desktop_config.json config.json; do
    [ -e "$CLAUDE3P/$item" ] && cp -R "$CLAUDE3P/$item" "$BACKUP_DIR/Claude-3p/"
  done
  {
    printf 'installed_at="%s"\n' "$(date '+%Y-%m-%d %H:%M:%S')"
    printf 'app_version="%s"\n' "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP_DIR/Contents/Info.plist" 2>/dev/null || echo unknown)"
    printf 'claude3p_existed="%s"\n' "$([ -d "$CLAUDE3P" ] && echo yes || echo no)"
    printf 'agent_existed="%s"\n' "$([ -f "$AGENT_PLIST" ] && echo yes || echo no)"
  } > "$MANIFEST"
  ok "已备份到 $BACKUP_DIR"
}

install_files() {
  [ -d "$GATEWAY_SRC" ] || die "缺少 gateway/ 目录（请从仓库运行本脚本）。"
  mkdir -p "$GATEWAY_DIR"
  cp "$GATEWAY_SRC"/*.py "$GATEWAY_DIR/"
  ok "网关代码：$GATEWAY_DIR"
}

install_agent() {
  if [ "$SKIP_AGENT" = 1 ]; then
    gateway_cli stop >/dev/null 2>&1 || true
    if gateway_cli start >/dev/null; then
      ok "网关：pidfile 方式（未安装 LaunchAgent）"
    else
      warn "网关启动失败，请查看日志：$INSTALL_DIR/gateway.log"
      return 1
    fi
    return 0
  fi
  mkdir -p "$(dirname "$AGENT_PLIST")"
  cat > "$AGENT_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$GATEWAY_DIR/ccd_gateway.py</string>
    <string>run</string>
    <string>--config</string>
    <string>$CONFIG_FILE</string>
  </array>
  <key>WorkingDirectory</key><string>$GATEWAY_DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$INSTALL_DIR/gateway.log</string>
  <key>StandardErrorPath</key><string>$INSTALL_DIR/gateway.log</string>
</dict>
</plist>
EOF
  launchctl bootout "gui/$UID/$LABEL" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$UID" "$AGENT_PLIST" >/dev/null 2>&1 \
    || launchctl load -w "$AGENT_PLIST" >/dev/null 2>&1 || true
  ok "LaunchAgent：$AGENT_PLIST"
}

remove_agent() {
  if [ "$SKIP_AGENT" = 1 ]; then
    rm -f "$AGENT_PLIST"
    return 0
  fi
  launchctl bootout "gui/$UID/$LABEL" >/dev/null 2>&1 \
    || launchctl unload -w "$AGENT_PLIST" >/dev/null 2>&1 || true
  rm -f "$AGENT_PLIST"
}

reload_app() {
  osascript -e 'tell application "Claude" to quit' >/dev/null 2>&1 || true
  sleep 4
  open -a Claude >/dev/null 2>&1 || true
}

restart_gateway() {
  if [ "$SKIP_AGENT" = 0 ] && launchctl print "gui/$UID/$LABEL" >/dev/null 2>&1; then
    gateway_cli start >/dev/null
  else
    gateway_cli stop >/dev/null 2>&1 || true
    gateway_cli start
  fi
}

do_install() {
  detect_env
  ensure_backup
  install_files
  config_cli show --require-key >/dev/null 2>&1 || ask_key
  [ -n "${OPENCODE_EFFORT:-}" ] && config_cli write --effort "$OPENCODE_EFFORT" >/dev/null
  [ -n "${OPENCODE_LOCAL_TITLE:-}" ] && config_cli write --local-title "$OPENCODE_LOCAL_TITLE" >/dev/null
  gateway_cli stop >/dev/null 2>&1 || true
  profile_cli apply --config "$CONFIG_FILE"
  install_agent
  local waited=0
  until gateway_cli status >/dev/null 2>&1; do
    waited=$((waited + 1))
    [ "$waited" -ge 10 ] && break
    sleep 0.7
  done
  gateway_cli status >/dev/null 2>&1 || warn "网关自检未通过，请查看日志：$INSTALL_DIR/gateway.log"
  reload_app
  info ""
  ok "安装完成，Claude Desktop 已重启并进入 3P（Gateway）模式。"
  info "状态：bash $SCRIPT_NAME --status    日志：$INSTALL_DIR/gateway.log"
  warn "还原：bash $SCRIPT_NAME --restore"
}

ask_key() {
  local input
  printf '请输入 OpenCode Go API Key（https://opencode.ai/zen 获取，sk- 开头）: ' >&2
  read -r input || true
  [ -n "$input" ] || die "未输入 API Key。"
  config_cli write --key "$input"
}

do_key() {
  detect_env
  local new_key="${1:-}"
  if [ -z "$new_key" ]; then
    config_cli show 2>/dev/null | grep -E "Key " || true
    printf '输入新的 OpenCode Go API Key（回车保持不变）: ' >&2
    read -r new_key || true
  fi
  [ -n "$new_key" ] || die "未提供新的 API Key。"
  config_cli write --key "$new_key"
  restart_gateway
  profile_cli apply --config "$CONFIG_FILE"
  ok "已更新 Key：$(mask_key "$new_key")"
}

do_tune() {
  detect_env
  local args=()
  [ -n "${TUNE_PORT:-}" ] && args+=(--port "$TUNE_PORT")
  [ -n "${TUNE_EFFORT:-}" ] && args+=(--effort "$TUNE_EFFORT")
  [ -n "${TUNE_LOCAL_TITLE:-}" ] && args+=(--local-title "$TUNE_LOCAL_TITLE")
  [ "${#args[@]}" -gt 0 ] || die "未提供要修改的项。"
  config_cli write "${args[@]}"
  restart_gateway
  profile_cli apply --config "$CONFIG_FILE"
  ok "设置已更新"
}

do_status() {
  detect_env
  config_cli show 2>/dev/null || warn "尚未安装（缺少 $CONFIG_FILE）"
  gateway_cli status 2>/dev/null || warn "网关未在运行"
  profile_cli status 2>/dev/null || warn "未找到 Claude-3p 配置"
  if [ "$SKIP_AGENT" = 1 ]; then
    warn "LaunchAgent：已跳过（非真实 HOME，网关走 pidfile）"
  elif launchctl print "gui/$UID/$LABEL" >/dev/null 2>&1; then
    ok "LaunchAgent：已加载"
  else
    warn "LaunchAgent：未加载"
  fi
}

do_restore() {
  detect_env
  # 同步仓库里的最新网关代码，保证 --restore 用的是当前版本
  [ -d "$GATEWAY_SRC" ] && install_files
  remove_agent
  gateway_cli stop >/dev/null 2>&1 || true
  if [ -d "$BACKUP_DIR/Claude-3p" ]; then
    original_existed="$(sed -n 's/^claude3p_existed="\(.*\)"/\1/p' "$MANIFEST" 2>/dev/null)"
    if [ "$original_existed" = "yes" ] || [ "$original_existed" = "no" ]; then
      profile_cli restore --backup-dir "$BACKUP_DIR" --original-existed "$original_existed"
    else
      profile_cli restore --backup-dir "$BACKUP_DIR"
    fi
  else
    warn "未找到安装前备份，跳过配置还原（不会删除 $CLAUDE3P 下的任何数据）"
  fi
  info "网关配置与备份保留在 $INSTALL_DIR（如需彻底清理可手动删除）"
  reload_app
  ok "还原完成，Claude Desktop 已重启。"
}

do_update() {
  detect_env
  local tmp src target
  tmp="$(mktemp -d -t ccd-update)"
  curl -fsSL "$REPO_TARBALL" | tar -xz -C "$tmp" || die "下载失败：$REPO_TARBALL"
  src="$tmp/${REPO_SLUG##*/}-${REPO_BRANCH}/$REPO_SUBDIR"
  [ -f "$src/$SCRIPT_NAME" ] || die "压缩包结构异常（未找到 $SCRIPT_NAME）"
  target="$INSTALL_DIR/$SCRIPT_NAME"
  mkdir -p "$INSTALL_DIR"
  cp "$src/$SCRIPT_NAME" "$target"
  chmod +x "$target"
  ok "已更新脚本：$target"
  bash "$src/$SCRIPT_NAME"
}

main() {
  TUNE_PORT=""; TUNE_EFFORT=""; TUNE_LOCAL_TITLE=""
  case "${1:-}" in
    ""|--install)   do_install ;;
    --key)          do_key "${2:-}" ;;
    --port)         TUNE_PORT="${2:-}"; do_tune ;;
    --effort)       TUNE_EFFORT="${2:-}"; do_tune ;;
    --local-title)  TUNE_LOCAL_TITLE="${2:-}"; do_tune ;;
    --status)       do_status ;;
    --restore)      do_restore ;;
    --update)       do_update ;;
    -h|--help)      usage ;;
    *)              usage; exit 1 ;;
  esac
}

main "$@"
