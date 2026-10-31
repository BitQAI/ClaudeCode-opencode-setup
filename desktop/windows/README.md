# Claude Desktop × OpenCode Go（Windows 版 · Node 实现）

> 本目录是 [ClaudeCode-opencode-setup](https://github.com/BitQAI/ClaudeCode-opencode-setup) **桌面端方案在 Windows 上的实现**。
> macOS 版请用 [`desktop/setup-desktop-opencode.sh`](../setup-desktop-opencode.sh)（bash + Python 3）。
> 两版语义完全一致（同一套路由、profile 结构、日志字段），只是运行时与自启方式不同。

```
Claude Desktop（第三方推理 / Gateway 模式）
        │  http://127.0.0.1:8799
        ▼
本地网关（Node 18+，仅监听 127.0.0.1）
        │  改写 model + 强制 effort=max + 注入会话头
        ▼
https://opencode.ai/zen/go/v1/messages
```

## 1. 前提

| 项目 | 要求 |
|---|---|
| 系统 | Windows 10 / 11 |
| Node | **18+（推荐 22）**，`node` 在 PATH 里 |
| Claude Desktop | 官方 Windows 版。注意 **Cowork 需要 `.msix`（商店/现代安装器）版本**，旧的 `.exe`（Squirrel）版没有 Cowork，会提示 "Reinstall the desktop app to access Cowork…"；`Code` 标签与统一面板不受影响 |
| OpenCode Go Key | `oc_sk-...`，在 <https://opencode.ai/zen> 获取 |

## 2. 安装

```bat
git clone https://github.com/BitQAI/ClaudeCode-opencode-setup.git
cd ClaudeCode-opencode-setup\desktop\windows

node setup.mjs                 :: 安装 / 更新（无 Key 时交互输入）
node setup.mjs --key           :: 查看 / 修改 Key（改了会自动重启网关）
node setup.mjs --status        :: 状态：配置 + profile + 计划任务 + 自检
node setup.mjs --restore       :: 还原（删计划任务/Run 键、还原 profile、停网关）
```

安装时脚本会：

1. 把 `%LOCALAPPDATA%\Claude-3p` 的配置项（`configLibrary`、`claude_desktop_config.json`、`config.json`）备份到
   `%USERPROFILE%\.claude-desktop-opencode\backup\`——只备份配置，不碰 `vm_bundles`/Cache 这类数 GB 的数据；
2. 把网关代码装到 `%USERPROFILE%\.claude-desktop-opencode\gateway\`，自安装一份 `setup.mjs` 到安装目录；
3. 写 3P profile：`inferenceProvider=gateway`、`inferenceGatewayBaseUrl=http://127.0.0.1:8799`、
   模型列表（含 `[1m]` 1M 档）、`desktopHome=no-vm`、`chatTabEnabled=true`、`coworkTabEnabled=false`；
4. 写自启：计划任务 `ClaudeDesktopOpenCodeGateway`（登录时启动守护）+ `HKCU\...\Run` 兜底；
5. 以 `supervise` 模式起网关（无控制台窗口，子进程挂了 3 秒后自动拉起）；
6. 重启 Claude Desktop。

> **不要**写 `HKCU\Software\Policies\Claude` 策略：一旦存在，桌面端会认为设备"受管"并锁死在管理员流程里（窗口打不开）。
> 需要的开关都写在 profile 里；脚本默认不写策略（`CCD_WRITE_POLICY=1` 才会写，一般用不到）。

## 3. 文件位置（与 macOS 的差异）

| 用途 | Windows | macOS |
|---|---|---|
| 安装目录 | `%USERPROFILE%\.claude-desktop-opencode\` | `~/.claude-desktop-opencode/` |
| 3P profile | `%LOCALAPPDATA%\Claude-3p\configLibrary\<uuid>.json` + `_meta.json` | `~/Library/Application Support/Claude-3p/configLibrary/...` |
| 部署模式开关 | `%LOCALAPPDATA%\Claude-3p\claude_desktop_config.json` → `{"deploymentMode":"3p"}` | 同结构 |
| 桌面端日志 | `%LOCALAPPDATA%\Claude-3p\logs\main.log` | `~/Library/Logs/...`（App 内可查） |
| 网关日志 | `%USERPROFILE%\.claude-desktop-opencode\gateway.log`（`gateway-console.log` 存 stdout） | 同左 |
| 自启 | 计划任务 + `HKCU\...\Run`，守护进程 `ccd_gateway.mjs supervise` | LaunchAgent（`KeepAlive`） |
| 运行时 | Node（`gateway/*.mjs`） | `python3`（`gateway/*.py`） |
| 终端/内置 Claude Code 配置 | `%USERPROFILE%\.claude\settings.json` | `~/.claude/settings.json` |

## 4. 模型映射

| 桌面端条目 | 选择器显示 | 上游真实模型 | 推理强度 |
|---|---|---|---|
| `claude-sonnet-4-5` | DeepSeek V4.1 Flash · max | `deepseek-v4.1-flash` | `max` |
| `claude-opus-4-5` | DeepSeek V4 Pro | `deepseek-v4-pro` | `max` |
| `claude-haiku-4-5` | DeepSeek V4 Flash | `deepseek-v4-flash` | `max` |
| `claude-sonnet-4-5[1m]` | DeepSeek V4.1 Flash · max · 1M | `deepseek-v4.1-flash` | `max` |
| `claude-opus-4-5[1m]` | DeepSeek V4 Pro · 1M | `deepseek-v4-pro` | `max` |
| `claude-haiku-4-5[1m]` | DeepSeek V4 Flash · 1M | `deepseek-v4-flash` | `max` |

`[1m]` 是 Claude Code 内置的 1M 上下文别名，网关为每条路由自动派生；CLI 发请求前会把它去掉并附加
`context-1m` beta，上游完全感知不到。**长会话请选带 `· 1M` 的档位**，原因见 `../README.md` §7：
不带它是 200K 窗口，自动压缩会在 **每 ~16.8 万 tokens** 触发一次。

## 5. 排错

| 现象 | 排查 |
|---|---|
| 桌面端底部没有 `Gateway` 标识 | `node setup.mjs --status` 看 profile 是否为 `gateway`；完全退出桌面端后重开；否则 `--restore` 再装 |
| 报 `Gateway was unreachable` / 请求全 502 | 网关没起来：`%USERPROFILE%\.claude-desktop-opencode\gateway-console.log` 看有没有 "Node.js not found"；`node setup.mjs --status` 自检 |
| `The provider rejected your credentials` | Key 无效/过期：`node setup.mjs --key oc_sk-...`（会自动重启网关） |
| `Invalid request / The request couldn't be completed.` + `{"model":"deepseek-v4.1-flash"}` | 会话 prompt 超过上游 **1,000,000 tokens**：OpenCode Go 只回空壳 400（无 `error.message`），同名小请求正常、`/v1/models` 与 `count_tokens` 也正常，所以不是模型名或网关的问题。`/compact` 或开新会话；根治见下一条 |
| 长会话每 ~16.8 万 tokens 就 compact | 自动压缩窗口被模型窗口钳制（`claude-sonnet-4-5` = 200K，阈值 = 200000 − 32000 = 168K）。改用 `· 1M` 档位；终端/内置 Claude Code 可另加 `CLAUDE_CODE_AUTO_COMPACT_WINDOW`（见根 README） |
| 想知道到底是哪种错 | `grep upstream_body "%USERPROFILE%\.claude-desktop-opencode\gateway.log"`：4xx/5xx 的上游响应体（截断 600 字）已记录；出现 `rewrite upstream 400 -> prompt_too_long` 表示网关已把它翻译成 `prompt is too long: N tokens > 1000000 maximum`，Claude Code 能据此自动压缩重试 |
| 改完配置没生效 | 配置改动会重启网关；桌面端需**完全退出**再打开（它缓存了 profile） |
| 想换端口 | `node setup.mjs --port 8899`，脚本会同步更新 profile 与启动器 |

## 6. 与 macOS 版的已知边界

- Windows 版**没有** `--update`（macOS 版会 `git ls-remote` 拉取自身）：更新直接 `git pull` 后重跑 `node setup.mjs`。
- Cowork（Windows 专属标签）依赖 `.msix` 安装包；`.exe` 版没有，profile 里已 `coworkTabEnabled=false` 降级。
- 桌面端升级后若调整 3P schema 或模型名校验，重跑 `node setup.mjs` 重新生成 profile 即可（备份里记了安装时的版本）。
- 日志一律脱敏：只记录路由名、改写后的模型、状态码、耗时与**上游错误体片段**，不记录 Key 与请求正文。
