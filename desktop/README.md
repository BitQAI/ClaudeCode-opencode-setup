# Claude Desktop × OpenCode Go（3P 本地网关）

> 本目录是 [ClaudeCode-opencode-setup](https://github.com/BitQAI/ClaudeCode-opencode-setup) 的**桌面端**部分。
> 终端 `claude` CLI 与 IDE 扩展请用仓库根目录的 `setup-claude-opencode.sh`。

让 **Claude Desktop**（官方 App，macOS）跑在 **OpenCode Go** 的 DeepSeek 模型上：
默认的 Sonnet 槽位对应 `deepseek-v4.1-flash`，推理强度固定 `max`。

```
Claude Desktop（第三方推理 / Gateway 模式）
        │  http://127.0.0.1:8799
        ▼
本地网关（Python 3 标准库，仅监听 127.0.0.1）
        │  改写 model + 强制 effort=max + 注入会话头
        ▼
https://opencode.ai/zen/go/v1/messages
```

一条命令安装，一条命令还原；上游 API Key 只存在本机配置里（权限 `600`），不写进 Claude 的 profile。

---

## 1. 为什么必须有一层本地转发

实测环境：Claude Desktop `1.52386.6`（`/Applications/Claude.app`），macOS。

| 想当然的做法 | 实测结果 | 结论 |
|---|---|---|
| 让桌面端直连 OpenCode Go，模型名写 `deepseek-v4.1-flash` | profile 能加载，但该条目被**模型名校验剔除**，选择器里只剩 Claude 风格的名字 | ✗ |
| 桌面端发 `claude-sonnet-4-5`，指望上游认这个名字 | 上游返回 `401 ModelError: Model ... is not supported` | ✗ |
| 桌面端直连第三方上游做凭据探测 | 日志 `[custom-3p] Gateway /v1/models non-OK status 404`，界面报 `The provider rejected your credentials` | ✗ |

三个约束同时成立：**桌面端只接受 Anthropic 风格模型名**、**上游只认自己的模型名**、**桌面端直连第三方会被凭据探测拦住**。
所以中间必须有一层转发，把"桌面端看到的 Claude 名"翻译成"上游认识的 deepseek 名"。

### 1.1 不需要自签证书

网上流传的绕过方案要求"本地 HTTP 必须套一层 HTTPS + 信任根证书"。
实测桌面端的网关地址校验是 **https，或 loopback 上的 http**（应用包内 `cs({allowLoopbackHttp:true})`），
因此 `http://127.0.0.1:8799` 直接可用，**不需要自签证书、不需要安装根证书**，对系统的侵入更小。

### 1.2 也不能靠"把某个模型名直接映射成 deepseek"

桌面端在把 profile 交给推理层之前会先做模型名校验，非 Anthropic 风格的条目会被剔除；
反过来上游也不认 `claude-*`。两侧的校验都在本地进程之外/之内各做一次，
没有配置文件层面的"改个名字"的余地，因此本方案选择最小改动：**在 loopback 上放一个只做翻译的进程**。

---

## 2. 模型映射

桌面端选择器显示的是下面的 label，实际请求发到上游时会被改写成右侧模型：

| 桌面端条目（Anthropic 名） | 选择器显示 | 上游真实模型 | 推理强度 |
|---|---|---|---|
| `claude-sonnet-4-5` | DeepSeek V4.1 Flash · max | `deepseek-v4.1-flash` | `max` |
| `claude-opus-4-5` | DeepSeek V4 Pro | `deepseek-v4-pro` | `max` |
| `claude-haiku-4-5` | DeepSeek V4 Flash | `deepseek-v4-flash` | `max` |

**默认槽位（Sonnet）就是 `deepseek-v4.1-flash` + `max`**，开箱即用。
三个槽位都能通过 `--effort` 统一改强度，或直接编辑 `~/.claude-desktop-opencode/config.json` 里的 `routes`。

---

## 3. 安装

前置：macOS、已安装 Claude Desktop、Python 3.8+。

```bash
git clone https://github.com/BitQAI/ClaudeCode-opencode-setup.git
cd ClaudeCode-opencode-setup/desktop
bash setup-desktop-opencode.sh
```

首次运行会提示输入 OpenCode Go API Key（在 <https://opencode.ai/zen> 获取，`sk-` 开头）。
安装脚本会：

1. 备份桌面端现有 3P 配置项（只备份 `configLibrary/` 与 `claude_desktop_config.json`、`config.json`，**不动** VM/Cache 数据）；
2. 把网关代码装到 `~/.claude-desktop-opencode/gateway/`，写入 `config.json`（权限 `600`）；
3. 写入 3P profile（`~/Library/Application Support/Claude-3p/configLibrary/<uuid>.json` + `_meta.json`），置 `deploymentMode = "3p"`；
4. 安装 LaunchAgent `com.bitqai.ccd-gateway`（`RunAtLoad` + `KeepAlive`，开机常驻）；
5. 重启 Claude Desktop。

装完在桌面端界面上应能看到底部为 `Gateway`、模型选择器显示 `DeepSeek V4.1 Flash · max`。

### 3.1 验证

```bash
bash setup-desktop-opencode.sh --status          # 配置 + 网关自检 + profile + LaunchAgent
curl -s http://127.0.0.1:8799/v1/models          # 应返回 3 条 route
python3 ~/.claude-desktop-opencode/gateway/ccd_gateway.py logs -n 40
```

在桌面端随便发一条消息，日志里会出现：

```
POST /v1/messages claude-sonnet-4-5 -> deepseek-v4.1-flash effort=max status=200 2181ms
```

---

## 4. 命令一览

| 命令 | 作用 |
|---|---|
| `bash setup-desktop-opencode.sh` | 安装 / 重新部署 |
| `bash setup-desktop-opencode.sh --status` | 查看网关、profile、LaunchAgent 状态（含 `GET /v1/models` 自检） |
| `bash setup-desktop-opencode.sh --key sk-xxxx` | 更新 OpenCode Go Key（不带参数则交互输入） |
| `bash setup-desktop-opencode.sh --port 8899` | 修改网关监听端口 |
| `bash setup-desktop-opencode.sh --effort high` | 修改推理强度：`low\|medium\|high\|xhigh\|max` |
| `bash setup-desktop-opencode.sh --local-title on` | 标题请求改为网关本地应答（默认 `off`） |
| `bash setup-desktop-opencode.sh --restore` | 还原到安装前状态并移除 LaunchAgent |
| `bash setup-desktop-opencode.sh --update` | 从 GitHub 拉取最新脚本并重新部署 |

网关也可单独驱动（`--config` 可指向自定义配置文件）：

```bash
python3 ~/.claude-desktop-opencode/gateway/ccd_gateway.py status
python3 ~/.claude-desktop-opencode/gateway/ccd_gateway.py start|stop|logs
```

---

## 5. 每个新会话的"第二笔调用"，以及 `--local-title`

实测发现：桌面端每**新建一个会话**，除了主回答还会多发一笔**会话标题生成**请求，特征是
`tools = []`、`messages` 只有 1 条、`output_config.format` 是 `json_schema{properties:{title}}`、
正文形如 `<session>…</session>` + "Write the title in the predominant language…"。
这笔请求走 Haiku 槽（→ `deepseek-v4-flash`），**同一会话只发生一次**，同一会话里追问不再触发。

因此你会在用量面板里看到成对的两次调用：

| 时间 | 模型 | 输入 | 输出 | 说明 |
|---|---|---|---|---|
| 17:45 | `deepseek-v4.1-flash` | 35367 | 79 | 主回答（Sonnet 槽） |
| 17:45 | `deepseek-v4-flash` | 2745 | 57 | 会话标题（Haiku 槽） |

这不是"网关重复调用"，而是桌面端自身的行为。若想省掉这一笔，打开本地应答：

```bash
bash setup-desktop-opencode.sh --local-title on
```

网关会识别上述特征并**不转发上游**，直接返回 `{"title": "…"}`（首行、截断 24 字符），
新建会话的上游调用数从 2 降到 1，回答与标题都正常。
**默认关闭**——保持与官方行为一致，避免任何本地猜测影响标题。

---

## 6. 配置与文件位置

| 路径 | 内容 |
|---|---|
| `~/.claude-desktop-opencode/config.json` | 监听地址/端口、上游地址、Key（`600`）、路由、`local_title_synthesis` |
| `~/.claude-desktop-opencode/gateway/` | 网关代码 |
| `~/.claude-desktop-opencode/gateway.log` | 脱敏访问日志 |
| `~/.claude-desktop-opencode/backup/` | 安装前的 3P 配置项备份 + `manifest.txt` |
| `~/Library/LaunchAgents/com.bitqai.ccd-gateway.plist` | 常驻服务定义 |
| `~/Library/Application Support/Claude-3p/configLibrary/` | 桌面端 3P profile（本脚本只维护自己那一条） |

---

## 7. 排错

| 现象 | 排查 |
|---|---|
| 桌面端底部没有出现 `Gateway` | `--status` 看 profile 是否为 `gateway`；确认完全退出（⌘Q）后重开；必要时 `--restore` 再重装 |
| 报 `The provider rejected your credentials` | Key 无效/过期：`--key sk-...` 更新后会自动重启网关 |
| 界面长时间转圈 | `tail -f ~/.claude-desktop-opencode/gateway.log`；若出现 `upstream unreachable`，检查网络与 `upstream_base_url` |
| 端口被占用 | `--port 8899` 换端口，脚本会同步更新 profile |
| 改完配置没生效 | 配置改动会触发 LaunchAgent 重载；若仍不生效，`bash setup-desktop-opencode.sh` 重跑一次 |
| 还原后想再回来 | 备份在 `~/.claude-desktop-opencode/backup/`，重跑无参安装即可 |
| `--restore` 后桌面端仍指向旧网关 | 备份是**安装那一刻**的快照。若安装前已被别的工具改过（例如把 `Default` 条目也写成了 gateway 配置），还原得到的就是那个状态。检查 `~/.claude-desktop-opencode/backup/Claude-3p/configLibrary/` 里 `Default` 的 profile：官方默认应为 `{}`，`claude_desktop_config.json` 不含 `deploymentMode` |

日志一律脱敏：只记录路由名、改写后的模型、状态码与耗时，**不记录 Key、Token 与正文**。

---

## 8. 安全与边界

- 只监听 `127.0.0.1`，不暴露到局域网；不使用系统代理；不改写 Claude Desktop 应用包或签名。
- 真实上游 Key 只出现在 `~/.claude-desktop-opencode/config.json`（`600`）；桌面端 profile 里放的是本地占位 key。
- 完全可逆：`--restore` 会把 profile 还原到安装前（只动本脚本维护的配置项，不触碰 `vm_bundles`、Cache 等数据）。
- 不做：伪造 Anthropic 登录/凭据、自动切换上游 provider。

已知边界：桌面端**升级后**若调整 3P schema 或模型名校验，profile 可能需要重新生成（重跑安装脚本即可）。
安装时会记录 Claude Desktop 版本，便于回溯。

---

## 9. 与终端版（Claude Code）的关系

同一个仓库里，"终端版"是根目录的 `setup-claude-opencode.sh`（写 `~/.claude/settings.json`），
"桌面端"是本目录的 `setup-desktop-opencode.sh`（写 Claude-3p profile + 本地网关）。
两者写不同的文件、互不影响，可以分别安装、分别还原。

桌面端内置的 Chat / Projects 由 Anthropic 服务端推理，不受本方案影响，也不需要重新登录。

## License

MIT
