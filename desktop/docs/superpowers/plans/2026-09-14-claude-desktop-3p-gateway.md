# 实施计划：Claude Desktop 3P 本地网关

输入：`docs/superpowers/specs/2026-09-14-claude-desktop-3p-gateway.md`

## T1 网关主体（gateway/ 四个模块，各自 ≤300 行）

按职责拆分（避免单文件超软上限）：

| 文件 | 职责 |
|---|---|
| `gateway/ccd_config.py` | 状态目录/路径、默认配置、读取与校验、路由索引 |
| `gateway/ccd_upstream.py` | 上游连接、请求头构造、session 头、标题请求识别与本地应答 |
| `gateway/ccd_lifecycle.py` | pidfile、start/stop/status/logs、LaunchAgent 无关的进程管理 |
| `gateway/ccd_gateway.py` | HTTP 处理器 + `run` 入口 + CLI 分发 |

- [ ] `load_config(path)`：读取并校验 `config.json`（listen/port/upstream/key/routes/effort/local_title_synthesis）
- [ ] `GET /v1/models` 输出 Anthropic 形状（只暴露 route 名）
- [ ] `POST /v1/messages` 改写 model/effort、注入 session 头、上游 `x-api-key`
- [ ] SSE 流式透传（chunked relay），非流式原样返回
- [ ] 上游错误/不可达 → Anthropic 形状错误体
- [ ] 标题请求本地应答：**`local_title_synthesis` 默认 `false`**（按用户决定）；开启时才拦截
- [ ] 访问日志（append，脱敏：不记 key/token/正文，只记 route、改写后模型、状态码、耗时）

验收：`curl` 三项（models / 非流式 / 流式 SSE）全部通过。

## T2 生命周期 CLI（gateway/ccd_gateway.py + setup 脚本）

- [ ] `ccd-gateway start|stop|status|logs|run`（run 为前台模式，供 LaunchAgent 使用）
- [ ] 端口占用检测；`status` 做 `GET /v1/models` 自检

验收：`status` 在运行时显示 route 数、上游、端口、pid；停止后显示 not running。

## T3 安装器（setup-desktop-opencode.sh）

文件：`setup-desktop-opencode.sh`（≤450 行）

- [ ] 前置检查：macOS、Claude Desktop 存在、Python 3、版本记录
- [ ] 备份：`Claude-3p/`（含 `_meta.json`、profile、`claude_desktop_config.json`）→ `~/.claude-desktop-opencode/backup-<ts>/`
- [ ] 写入网关配置（600）并启动网关
- [ ] profile 事务：固定自己的 UUID entry，合并进 `_meta.json`，写 `deploymentMode=3p`
- [ ] LaunchAgent 安装（RunAtLoad + KeepAlive）
- [ ] `--key/--port/--effort/--routes/--status/--restore/--update`
- [ ] 提示"需完全退出并重启 Claude Desktop"

验收：安装后桌面端底部显示 `Gateway`、模型选择器显示 label；`--restore` 后回到原状态。

## T4 文档与仓库

- [ ] `README.md` / `README.en.md`：机制说明、与"不挂端口"方案的对比（含实测证据）、安装/还原/排错
- [ ] `.gitignore`
- [ ] 用户确认后：`git init` + commit + 推送到 GitHub（新仓库名待定）

## T5 端到端回归（每项都要真实证据）

- [ ] 桌面端发送真实消息 → 网关日志 + 上游 200
- [ ] 默认（标题本地应答关）：新建会话产生 2 次上游调用（主回答 + 标题）
- [ ] 打开 `local_title_synthesis` 后：新建会话上游调用数 2 → 1，回答与标题均正常
- [ ] 图片/长上下文各一次
- [ ] `--restore` 回归
- [ ] 记录到 Spec 的"验证标准"清单

## 顺序与依赖

T1 → T2 → T3 → T5 → T4（文档随实现同步更新，最后统一提交）。
