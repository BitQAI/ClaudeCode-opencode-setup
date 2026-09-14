# Claude Code × OpenCode Go（deepseek-v4.1-flash，原生多模态）— 部署方案

[中文](/README.md) | [English](/README.en.md)

在新电脑上快速配置 **Claude Code** 使用 **OpenCode Go 订阅**的模型作为主力。默认主模型 `deepseek-v4.1-flash`（原生支持文本 + 图片），Opus/Sonnet/Haiku 三档模型名分别映射到 Go 订阅里的强/中/快模型。

与 [codex-opencode-setup](https://github.com/BitQAI/codex-opencode-setup) 是姊妹项目：那边配置 Codex，这边配置 Claude Code，共用同一个 OpenCode Go API Key。

本仓库包含两部分，按需取用：

| 目录 | 目标 | 写到哪里 |
|---|---|---|
| 根目录（`setup-claude-opencode.sh`） | 终端 `claude` CLI、IDE 扩展、桌面端内置的 Claude Code 会话 | `~/.claude/settings.json` |
| [`desktop/`](/desktop)（`setup-desktop-opencode.sh`） | Claude Desktop 的 **Chat / Cowork**（第三方推理 3P 本地网关） | `Claude-3p` profile + `~/.claude-desktop-opencode/` |

另附 [`CLAUDE.md`](/CLAUDE.md)：本仓库使用的工程准则（Claude Code 在这个目录下会自动读取），与 `~/.claude/CLAUDE.md` 保持同一份内容。
注意它**只管 Claude Code**：桌面端 Chat / Cowork 不读文件系统里的 `CLAUDE.md`，要用设置里的 **Instructions for Claude**（存储位置与形式见 [desktop/README.md 6.1](/desktop/README.md)）。

---

## 一、架构总览

```
Claude Code (CLI / 桌面)
  │  ~/.claude/settings.json → env.ANTHROPIC_BASE_URL = https://opencode.ai/zen/go
  │  Claude Code 原生 Anthropic Messages 协议，无需任何中间件
  ▼
https://opencode.ai/zen/go/v1/messages   (OpenCode Go 订阅，Anthropic 兼容端点)
  ├── deepseek-v4.1-flash   主模型（原生多模态：文本 + 图片）
  ├── deepseek-v4-pro       Opus 档
  ├── deepseek-v4-flash     Haiku 档（快、便宜，背景小任务）
  └── glm-5.2 / kimi-k3 / qwen3.8-max / minimax-m3 …（Go 订阅内其它模型）
```

**设计要点**

- **零中间件**：Claude Code 直接说 Anthropic 协议，OpenCode Go 原生提供 `/v1/messages`，不需要 claude-code-router 之类的转发进程。
- **只改 `settings.json` 的 `env` 段**：不碰 hooks、permissions、MCP、statusline 等既有配置（合并写入，未知字段原样保留）。
- **可一键还原**：安装前自动备份 `settings.json` 与 `~/.claude.json`，`--restore` 精确回滚。

---

## 二、快速开始（一键脚本，推荐）

### 前提

1. 已安装 Claude Code，且**至少启动过一次**（生成 `~/.claude/` 与 `~/.claude.json`）：
   `npm install -g @anthropic-ai/claude-code` 或官方原生安装包，`claude --version` 能出版本号
2. 有 **OpenCode Go 订阅**的 API Key（`sk-` 开头，在 https://opencode.ai/zen 获取）
3. 本机有 **Python 3.8+**（脚本用它安全地合并 JSON）

### macOS / Linux / Windows(Git Bash)

```bash
# 方式一：curl 一键安装（从 GitHub）
bash <(curl -fsSL https://raw.githubusercontent.com/BitQAI/ClaudeCode-opencode-setup/main/setup-claude-opencode.sh)

# 方式二：本地脚本
cd ClaudeCode-opencode-setup && bash setup-claude-opencode.sh
```

首次运行会要求输入 API Key；已安装过再次运行会重新备份并覆盖配置。

### 脚本做了什么

1. 备份 `~/.claude/settings.json` 与 `~/.claude.json` 到 `~/.claude/backup-claude-opencode/`，并写 `manifest.txt` 记录安装前的状态
2. 校验 API Key（向 `/v1/messages` 发一次真实请求，期望 HTTP 200）
3. 把 Key 写入 `~/.claude/claude-opencode-setup/.env`（权限 `600`）
4. **合并**写入 `~/.claude/settings.json` 的 `env` 段（见下），并移除其中的 `ANTHROPIC_AUTH_TOKEN`
5. 在 `~/.claude.json` 的 `customApiKeyResponses.approved` 里登记该 Key（末尾 20 位），避免交互式启动时弹"是否使用此 API Key"确认框
6. 把脚本自身安装到 `~/.claude/claude-opencode-setup/`，便于以后 `--key` / `--status` / `--update`

### 写入后的 `~/.claude/settings.json`

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://opencode.ai/zen/go",
    "ANTHROPIC_API_KEY": "sk-你的opencode-go-key",
    "ANTHROPIC_MODEL": "deepseek-v4.1-flash",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "deepseek-v4-pro",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "deepseek-v4.1-flash",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-v4-flash",
    "CLAUDE_CODE_SUBAGENT_MODEL": "deepseek-v4.1-flash",
    "CLAUDE_CODE_EFFORT_LEVEL": "max"
  }
}
```

> 原有 `permissions` / `hooks` / `model` / `skipDangerousModePermissionPrompt` 等字段保持不变。

### 模型档位映射（默认档 = Sonnet）

| Claude Code 档位 | 实际模型 | 说明 |
|---|---|---|
| **默认 / Sonnet** | `deepseek-v4.1-flash` | 主力档，**推理强度 max**，原生多模态（文本 + 图片） |
| Opus（`/model opus`） | `deepseek-v4-pro` | 更强推理档 |
| Haiku | `deepseek-v4-flash` | 背景小任务（标题生成、探测、摘要），快且便宜 |
| 子代理 | `deepseek-v4.1-flash` | 由 `CLAUDE_CODE_SUBAGENT_MODEL` 控制 |

**推理强度 max** 由 `CLAUDE_CODE_EFFORT_LEVEL=max` 控制（它覆盖 `settings.json` 里的 `effortLevel`，后者合法取值只有 `low|medium|high|xhigh`）。抓包确认 Claude Code 会把它作为请求体字段发出，OpenCode Go 接受并正常返回：

```json
{"model":"deepseek-v4.1-flash","thinking":{"type":"adaptive"},"output_config":{"effort":"max"}, ...}
```

改强度：`--effort low|medium|high|xhigh|max`。

### 桌面端（Claude Desktop 内置的 Claude Code）同样生效

`/Applications/Claude.app` 里的 Claude Code 会话与本仓库的 CLI 配置共用同一套用户设置：

1. 它创建会话时用的 `settingSources` 是 `['user','project','local']`，即**读取 `~/.claude/settings.json`**；
2. 实测 `settings.json` 的 `env` **优先级高于进程环境变量**（把 `ANTHROPIC_BASE_URL`/`ANTHROPIC_API_KEY` 设成垃圾值，Claude Code 仍按 settings.json 走 opencode 并成功返回），因此桌面端注入自己的 provider 环境也不会顶掉这份配置；
3. 桌面端设置里的"环境变量"页（`ccd-environment-config`，内容用系统钥匙串加密存储）因此**不需要再填**，一般保持为空即可；
4. 改完配置需**完全退出并重启 Claude.app**，否则它继续用旧的环境快照。

> 注意区分：以上说的是桌面端里跑的 **Claude Code 会话**（`Code` 标签，读 `~/.claude/settings.json`）。
> 桌面端的 **Chat / Cowork** 走的是服务端推理，需要 [`desktop/`](/desktop) 里的 3P 本地网关方案，
> 两者互不影响，可分别安装。

### 完成

重启 Claude Code（新开一个终端窗口）即可，进入任意项目目录执行 `claude`。

---

## 三、两个容易踩坑的关键点（实测得出）

### 1. 必须用 `ANTHROPIC_API_KEY`，不要用 `ANTHROPIC_AUTH_TOKEN`

DeepSeek 官方文档（[接入 Claude Code](https://api-docs.deepseek.com/zh-cn/quick_start/agent_integrations/claude_code)）用的是 `ANTHROPIC_AUTH_TOKEN`，因为 `api.deepseek.com/anthropic` 接受 `Authorization: Bearer`。

**OpenCode Go 的 `/v1/messages` 只认 `x-api-key`**，收到 Bearer 会直接 `401`：

```bash
# Authorization: Bearer → 401
$ curl -s -o /dev/null -w '%{http_code}\n' https://opencode.ai/zen/go/v1/messages \
    -H "Authorization: Bearer $KEY" ...
401
{"type":"error","error":{"type":"AuthError","message":"Missing API key."}}
```

Claude Code 的行为由环境变量决定：设 `ANTHROPIC_API_KEY` → 发 `x-api-key`；设 `ANTHROPIC_AUTH_TOKEN` → 发 `Authorization: Bearer`。所以本方案写入 `ANTHROPIC_API_KEY`，并主动清理遗留的 `ANTHROPIC_AUTH_TOKEN`。

### 2. session 头不用自己造，Claude Code 自带

OpenCode Go 要求每个请求带稳定会话 ID（`x-opencode-session`），否则返回 `400 MissingSessionID`；
但官方[已验证客户端列表](https://opencode.ai/docs/go/#validated-clients)里写明 **Claude Code 会被识别其原生 session 头，无需任何自定义头包装**。抓包确认 Claude Code 2.1.x 每个请求都带：

```
x-claude-code-session-id: 8c15310e-3605-4744-a0f9-8d6d710c4b59
x-api-key: sk-xxxx
user-agent: claude-cli/2.1.197 (external, sdk-cli)
```

因此本方案不需要代理层，也不需要 `ANTHROPIC_CUSTOM_HEADERS`。

---

## 四、手动配置（不跑脚本时）

```bash
# 1) 直接写 settings.json（也可用 export 临时生效）
mkdir -p ~/.claude
python3 - <<'PY'
import json, os, pathlib
p = pathlib.Path.home() / ".claude" / "settings.json"
data = json.loads(p.read_text()) if p.exists() else {}
data.setdefault("env", {}).update({
    "ANTHROPIC_BASE_URL": "https://opencode.ai/zen/go",
    "ANTHROPIC_API_KEY": "sk-你的opencode-go-key",
    "ANTHROPIC_MODEL": "deepseek-v4.1-flash",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "deepseek-v4-pro",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "deepseek-v4.1-flash",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-v4-flash",
    "CLAUDE_CODE_SUBAGENT_MODEL": "deepseek-v4.1-flash",
})
data["env"].pop("ANTHROPIC_AUTH_TOKEN", None)
p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
os.chmod(p, 0o600)
PY

# 2) 授权该 Key（避免交互确认框），suffix 为 Key 最后 20 位
python3 - <<'PY'
import json, os, pathlib
key = "sk-你的opencode-go-key"
p = pathlib.Path.home() / ".claude.json"
data = json.loads(p.read_text()) if p.exists() else {}
resp = data.setdefault("customApiKeyResponses", {})
approved = [x for x in resp.get("approved", []) if x != key[-20:]]
approved.append(key[-20:])
resp["approved"] = approved
resp["rejected"] = [x for x in resp.get("rejected", []) if x != key[-20:]]
p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
os.chmod(p, 0o600)
PY
```

临时验证（不改文件）：

```bash
ANTHROPIC_BASE_URL=https://opencode.ai/zen/go \
ANTHROPIC_API_KEY=sk-你的key \
ANTHROPIC_MODEL=deepseek-v4.1-flash \
claude -p "Reply with exactly: PONG"
```

---

## 五、常用命令

脚本安装后会固定在 `~/.claude/claude-opencode-setup/setup-claude-opencode.sh`：

```bash
# 查看/修改 API Key（交互式，显示脱敏后的当前 Key）
bash ~/.claude/claude-opencode-setup/setup-claude-opencode.sh --key
bash ~/.claude/claude-opencode-setup/setup-claude-opencode.sh --key sk-xxxx

# 设置推理强度（默认 max；也可用 low|medium|high|xhigh）
bash ~/.claude/claude-opencode-setup/setup-claude-opencode.sh --effort max

# 查看当前配置 + 端点连通性自检
bash ~/.claude/claude-opencode-setup/setup-claude-opencode.sh --status

# 拉取远程最新脚本并重新部署（git 仓库则 fast-forward 拉取）
bash ~/.claude/claude-opencode-setup/setup-claude-opencode.sh --update

# 还原到安装前状态
bash ~/.claude/claude-opencode-setup/setup-claude-opencode.sh --restore
```

> 更换 Key 后需重启 Claude Code 生效。

---

## 六、验证（可自行复现）

```bash
# 1) 端点连通性（期望 HTTP 200，返回 Anthropic 格式 message）
KEY=$(grep -o '[^=]*$' ~/.claude/claude-opencode-setup/.env)
curl -s -o /dev/null -w '%{http_code}\n' https://opencode.ai/zen/go/v1/messages \
  -H "x-api-key: $KEY" -H "anthropic-version: 2023-06-01" -H "content-type: application/json" \
  -H "x-claude-code-session-id: $(uuidgen)" \
  -d '{"model":"deepseek-v4.1-flash","max_tokens":16,"messages":[{"role":"user","content":"ping"}]}'

# 2) Claude Code 端到端（普通问答 / 工具调用 / 子代理模型）
claude -p "Reply with exactly: PONG" --output-format json
claude -p "Use the Bash tool to run exactly: echo TOOL_OK" --allowedTools Bash --output-format json

# 3) 图片（多模态，走 Anthropic image block）
#    实测：发一张纯红色 64x64 PNG → 模型回答 "Red"
```

安装脚本 `--status` 也会自动做第 1 步。

### 本仓库的实测记录（2026-09-14，macOS + Claude Code 2.1.197 + OpenCode Go）

| 验证项 | 命令 | 结果 |
|---|---|---|
| 端点可用 | `POST /v1/messages`（`x-api-key` + session 头） | `200`，返回 `{"type":"message",...}` |
| Bearer 不被接受 | 同上但改 `Authorization: Bearer` | `401 AuthError: Missing API key` |
| 缺 session 头 | 有 `x-api-key`、无 session 头 | `400 MissingSessionID`（Claude Code 自身会带，故实际不会遇到） |
| Claude Code 直连问答 | `claude -p "Reply with exactly: PONG"`（仅靠 settings.json） | `is_error:false`，`result:"PONG"`，`deepseek-v4.1-flash` |
| 工具调用 | `claude -p "... echo OPENCODE_TOOL_OK" --allowedTools Bash` | 2 turns，`result:"OPENCODE_TOOL_OK"` |
| Haiku 档映射 | 同一次会话的辅助请求 | 命中 `deepseek-v4-flash`（`modelUsage` 可见） |
| 图片理解 | image block → `deepseek-v4.1-flash` | `"Red"` |
| 模型可用性 | `deepseek-v4.1-flash` / `deepseek-v4-flash` / `deepseek-v4-pro` | 均 `200` |
| 默认档 = Sonnet 映射 | 会话默认 `model: sonnet` | 实际请求模型 `deepseek-v4.1-flash` |
| 推理强度 max | `CLAUDE_CODE_EFFORT_LEVEL=max` | 请求体含 `output_config.effort="max"` + `thinking: adaptive`，`200` 返回 `PONG`（无告警） |
| settings 覆盖进程环境 | 垃圾 `ANTHROPIC_BASE_URL`/`API_KEY` + 正常 settings | 仍走 opencode 成功（`is_error:false`） |
| 桌面端会话设置源 | `Claude.app` 内 `settingSources=['user','project','local']` | 读取 `~/.claude/settings.json` |

---

## 七、故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| `401 Missing API key` | 用了 `ANTHROPIC_AUTH_TOKEN`（发 Bearer），或 Key 不是 OpenCode Go 的 | 确保 `settings.json` 里是 `ANTHROPIC_API_KEY`，并删掉 `ANTHROPIC_AUTH_TOKEN`；重跑 `--key` 校验 |
| `400 MissingSessionID` | 请求没带会话头（多见于自写脚本/代理转发掉了头） | 用 Claude Code 本体请求；自测 curl 请显式加 `x-claude-code-session-id`；自建代理必须透传该头 |
| 交互式启动弹"是否使用此 API Key" | `customApiKeyResponses.approved` 未登记 | 重跑安装脚本（会自动登记 Key 末 20 位），或答 yes |
| 每个请求都报认证失败、但 curl 正常 | 终端里残留 `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_BASE_URL` 旧值 | `env | grep ANTHROPIC` 检查并清理 shell 配置文件 |
| `403 error code: 1010` | Cloudflare 拦截裸 `urllib`/默认 UA 的请求 | 自测脚本请用 `curl` 或带正常 UA，勿用默认 Python UA |
| 上下文不够用 | 各 Go 模型上下文 200K | Claude Code 会按窗口自动压缩；长任务建议开新会话或 `/compact` |
| 改了 Key 不生效 | Claude Code 启动时读取配置 | 重启 Claude Code（新开终端） |

---

## 八、与 Codex 方案的对照

| 项 | codex-opencode-setup | 本项目（ClaudeCode-opencode-setup） |
|---|---|---|
| 配置文件 | `~/.codex/config.toml` | `~/.claude/settings.json` |
| 端点 | `https://opencode.ai/zen/go/v1`（Responses 协议） | `https://opencode.ai/zen/go`（Anthropic Messages 协议） |
| 认证字段 | `experimental_bearer_token`（Bearer） | `ANTHROPIC_API_KEY`（`x-api-key`） |
| 主模型 | `deepseek-v4.1-flash` | `deepseek-v4.1-flash` |
| 图片 | 原生多模态 | 原生多模态（Anthropic image block） |
| 备份目录 | `~/.codex/backup-opencode-codex/` | `~/.claude/backup-claude-opencode/` |
| 还原 | `--restore` | `--restore` |

---

## 九、安全提示

- **API Key 落盘位置**：`~/.claude/claude-opencode-setup/.env`（`600`）与 `~/.claude/settings.json`（`600`）。请勿把这两个文件提交到任何仓库。
- **`~/.claude.json` 会被改写**：脚本只调整 `customApiKeyResponses`，并保留其它字段；安装前已备份到 `~/.claude/backup-claude-opencode/claude.json`。
- **不要把 Key 写进 `CLAUDE.md` / 项目文件**：Claude Code 会把项目文件内容发给模型。
- 脚本不会把 Key 硬编码进自身，分享脚本前无需脱敏（Key 是运行时输入的）。

---

## 十、范围与已知限制

- 目前**只提供 bash 版脚本**（macOS / Linux / Windows Git Bash 均可用，脚本内已做 Windows 路径与 `python` 命令适配）。PowerShell 版尚未提供：本机没有 Windows/PowerShell 环境，无法验证，按"没验证不发"的原则暂不附上。
- 默认模型映射针对 OpenCode Go 订阅中的 DeepSeek 系列；若要用 `glm-5.2`、`kimi-k3` 等，直接改 `settings.json` 里对应的模型名即可（`/models` 或 `curl $BASE_URL/v1/models` 可列出全部可用模型）。
- 端点与模型清单由 OpenCode 控制，可能变动；`deepseek-v4.1-flash` 为当前默认。

---

## 十一、桌面端（Claude Desktop 的 Chat / Cowork）

Claude Desktop 的 Chat / Projects / Cowork 是 **Anthropic 服务端推理**，不读 `~/.claude/settings.json`，
所以上面的 CLI 方案管不到它。桌面端从 `1.40609` 起支持官方"第三方推理（3P）"模式，但实测有三个硬约束：
桌面端**只接受 Anthropic 风格模型名**、OpenCode Go **不认 `claude-*` 名字**、桌面端**直连第三方会被凭据探测拦住**。

因此 [`desktop/`](/desktop) 提供一个小型本地网关：桌面端看到的仍是 `claude-sonnet-4-5` / `claude-opus-4-5` /
`claude-haiku-4-5`，请求到网关后被改写成 `deepseek-v4.1-flash` / `deepseek-v4-pro` / `deepseek-v4-flash`
并强制 `effort=max`，再转发到 OpenCode Go。**Sonnet 槽位默认就是 `deepseek-v4.1-flash` + `max`。**

```bash
git clone https://github.com/BitQAI/ClaudeCode-opencode-setup.git
cd ClaudeCode-opencode-setup/desktop
bash setup-desktop-opencode.sh           # 安装（首次会问 Key）
bash setup-desktop-opencode.sh --status  # 查看状态
bash setup-desktop-opencode.sh --restore # 一键还原
```

细节、实测证据与排错见 [desktop/README.md](/desktop/README.md)：

- 网关只监听 `127.0.0.1`，Key 存在 `~/.claude-desktop-opencode/config.json`（`600`），桌面端 profile 里放的是占位 Key；
- **不需要自签证书**：桌面端接受 loopback 上的 http（实测 `allowLoopbackHttp: true`），比"必须套 HTTPS"的方案少动一层系统；
- 每个新会话会多一笔"标题生成"请求（走 Haiku 槽）；默认保持官方行为，`--local-title on` 可让网关本地应答，省掉这一笔。
