# 实施计划：Claude Desktop 3P 本地网关

输入：`docs/superpowers/specs/2026-09-14-claude-desktop-3p-gateway.md`

> 状态：**已完成**（2026-09-14）。代码与文档并入 `BitQAI/ClaudeCode-opencode-setup` 的 `desktop/` 子目录。

## T1 网关主体（gateway/ 四个模块，各自 ≤300 行）

按职责拆分（避免单文件超软上限）：

| 文件 | 职责 |
|---|---|
| `gateway/ccd_config.py` | 状态目录/路径、默认配置、读取与校验、路由索引 |
| `gateway/ccd_upstream.py` | 上游连接、请求头构造、session 头、标题请求识别与本地应答 |
| `gateway/ccd_lifecycle.py` | pidfile、start/stop/status/logs、LaunchAgent 感知的进程管理 |
| `gateway/ccd_gateway.py` | HTTP 处理器 + `run` 入口 + CLI 分发 |

- [x] `load_config(path)`：读取并校验 `config.json`（listen/port/upstream/key/routes/effort/local_title_synthesis）
- [x] `GET /v1/models` 输出 Anthropic 形状（只暴露 route 名）
- [x] `POST /v1/messages` 改写 model/effort、注入 session 头、上游 `x-api-key`
- [x] SSE 流式透传（chunked relay），非流式原样返回
- [x] 上游错误/不可达 → Anthropic 形状错误体
- [x] 标题请求本地应答：**`local_title_synthesis` 默认 `false`**（按用户决定）；开启时才拦截
- [x] 访问日志（append，脱敏：不记 key/token/正文，只记 route、改写后模型、状态码、耗时）

验收：`curl` 三项（models / 非流式 / 流式 SSE）全部通过。

## T2 生命周期 CLI（gateway/ccd_gateway.py + setup 脚本）

- [x] `ccd-gateway start|stop|status|logs|run`（run 为前台模式，供 LaunchAgent 使用）
- [x] 端口占用检测；`status` 做 `GET /v1/models` 自检
- [x] `start/stop/status` 感知 LaunchAgent（`kickstart -k` 重载、`bootout` 停止），不与 pidfile 冲突

验收：`status` 在运行时显示 route 数、上游、端口、pid/LaunchAgent；停止后显示 not running。

## T3 安装器（setup-desktop-opencode.sh）

文件：`setup-desktop-opencode.sh`（实际 300 行，≤450 行上限）

- [x] 前置检查：macOS、Claude Desktop 存在、Python 3、版本记录
- [x] 备份：只备份配置项（`configLibrary`、`claude_desktop_config.json`、`config.json`），**不复制** 数 GB 的 `vm_bundles`
- [x] 写入网关配置（600）并由 LaunchAgent 启动网关
- [x] profile 事务：新增自己的 UUID entry（保留 `Default`），合并进 `_meta.json`，写 `deploymentMode=3p`
- [x] LaunchAgent 安装（RunAtLoad + KeepAlive）
- [x] `--key/--port/--effort/--local-title/--status/--restore/--update`
- [x] `--restore` 前同步最新代码，还原只处理配置项
- [x] 假 HOME 保护：`HOME` 非真实用户目录时跳过 LaunchAgent，降级 pidfile
- [x] 提示"需完全退出并重启 Claude Desktop"

验收：安装后桌面端底部显示 `Gateway`、模型选择器显示 label；`--restore` 后回到原状态。

## T4 文档与仓库

- [x] `README.md` / `README.en.md`：机制说明、与"不挂端口"方案的对比（含实测证据）、安装/还原/排错
- [x] `.gitignore`
- [x] 用户确认后：并入现有仓库 `BitQAI/ClaudeCode-opencode-setup` 的 `desktop/` 子目录并推送（新开仓库的方案作废）

## T5 端到端回归（每项都要真实证据）

- [x] 桌面端发送真实消息 → 网关日志 + 上游 200
- [x] 默认（标题本地应答关）：新建会话产生 2 次上游调用（主回答 + 标题）
- [x] 打开 `local_title_synthesis` 后：新建会话上游调用数 2 → 1，回答与标题均正常
- [x] 图片/长上下文各一次（流式与非流式均验证；opus / haiku 槽位各一次）
- [x] `--restore` 回归（真机：profile 回 `Default`、LaunchAgent 卸载、8799 释放、plist 删除）
- [x] 记录到 Spec 的"验证标准"清单

## 顺序与依赖

T1 → T2 → T3 → T5 → T4（文档随实现同步更新，最后统一提交）。
