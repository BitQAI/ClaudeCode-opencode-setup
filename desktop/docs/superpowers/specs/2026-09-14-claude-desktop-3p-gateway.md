# Claude Desktop × OpenCode Go 本地网关（3P 模式）— 设计规格

日期：2026-09-14　状态：**已实施并验证**（代码并入 `BitQAI/ClaudeCode-opencode-setup` 的 `desktop/`）

## 1. 问题背景

Claude Desktop（`com.anthropic.claudefordesktop`，本机 1.52386.6）支持官方"第三方推理"（3P）模式，
配置入口是 `~/Library/Application Support/Claude-3p/configLibrary/` 下的 profile：

- `_meta.json`：`appliedId` + `entries[]`
- `configLibrary/<uuid>.json`：被应用的配置文档
- `Claude-3p/claude_desktop_config.json`：`deploymentMode = "3p" | "1p"`

目标是让桌面端跑在 **OpenCode Go** 的 `deepseek-v4.1-flash`（推理强度 max）上，而不是 Anthropic 官方模型。

## 2. 实测结论：为什么"不挂端口"不可行

| 假设做法 | 实测结果 | 结论 |
|---|---|---|
| 直接让桌面端连 `https://opencode.ai/zen/go`，模型名写 `deepseek-v4.1-flash` | profile 被加载，但该条目被**模型名校验剔除**，选择器只剩 Claude 风格的名字 | ✗ |
| 让桌面端发 Claude 名（`claude-sonnet-4-5`），靠上游改名 | OpenCode Go 返回 `401 ModelError: Model ... is not supported` | ✗ |
| 桌面端直连第三方上游做凭据探测 | 日志 `[custom-3p] Gateway /v1/models non-OK status 404`（Cloudflare 返回 HTML），界面报 "The provider rejected your credentials" | ✗ |

三个约束同时在位：**桌面端只允许 Anthropic 风格模型名**、**上游不认 Claude 名**、**桌面端直连第三方会被 Cloudflare/凭据探测拦住**。
因此必须有一层本地转发，把"桌面端看到的 Claude 名"翻译成"上游认识的 deepseek 名"。

## 3. 方案概要

```
Claude Desktop (3P / Gateway)
  │  inferenceGatewayBaseUrl = http://127.0.0.1:<port>
  │  发送：claude-sonnet-4-5 / claude-opus-4-5 / claude-haiku-4-5
  ▼
本地网关（纯 Python 标准库，仅监听 127.0.0.1）
  │  · GET  /v1/models         → 返回 Claude 风格的安全 route 列表
  │  · POST /v1/messages       → 改写 model + 强制 effort=max + 注入 session 头
  ▼
https://opencode.ai/zen/go/v1/messages（x-api-key 认证）
  └── deepseek-v4.1-flash / deepseek-v4-pro / deepseek-v4-flash
```

**关键实测发现（与参考方案不同）**：桌面端的 gateway 地址校验是"https，或 loopback 上的 http"
（`cs({allowLoopbackHttp:true})`）。因此 **`http://127.0.0.1:<port>` 可用，不需要自签证书、不需要信任根证书**，
比"必须套 HTTPS"的方案更少侵入系统。

## 4. 技术选型

| 项 | 选择 | 理由 |
|---|---|---|
| 语言/依赖 | Python 3 标准库（`http.server` + `http.client` + `ssl` 可选） | 与现有两个 setup 仓库一致：零新增依赖、易审阅 |
| 监听地址 | 固定 `127.0.0.1` | 不暴露到局域网；避免"被当成本地代理服务"滥用 |
| 传输 | loopback HTTP（已验证）；预留 `--tls` 选项 | 桌面端接受 loopback http；HTTPS 仅作兼容退路 |
| 常驻方式 | `~/Library/LaunchAgents/com.bitqai.ccd-gateway.plist`（RunAtLoad + KeepAlive） | 桌面端启动时网关必须已在监听 |
| 密钥存放 | 网关配置 `~/.claude-desktop-opencode/config.json`（600），profile 里只放本地假 key | 真实上游 key 不写进 Desktop profile |

## 5. 数据模型

`~/.claude-desktop-opencode/config.json`

```json
{
  "listen": "127.0.0.1",
  "port": 8799,
  "upstream_base_url": "https://opencode.ai/zen/go",
  "upstream_api_key": "sk-...",
  "default_effort": "max",
  "routes": [
    {"name": "claude-sonnet-4-5", "label": "DeepSeek V4.1 Flash · max", "model": "deepseek-v4.1-flash", "effort": "max"},
    {"name": "claude-opus-4-5",   "label": "DeepSeek V4 Pro",          "model": "deepseek-v4-pro",   "effort": "max"},
    {"name": "claude-haiku-4-5",  "label": "DeepSeek V4 Flash",        "model": "deepseek-v4-flash", "effort": "max"}
  ]
}
```

写入桌面端 profile（`Claude-3p/configLibrary/<uuid>.json`）：

```json
{
  "inferenceProvider": "gateway",
  "inferenceGatewayBaseUrl": "http://127.0.0.1:8799",
  "inferenceGatewayApiKey": "local-gateway-key",
  "inferenceGatewayAuthScheme": "x-api-key",
  "inferenceModels": [{"name": "claude-sonnet-4-5", "labelOverride": "DeepSeek V4.1 Flash · max"}, "..."]
}
```

## 6. API 设计

| 端点 | 行为 |
|---|---|
| `GET /v1/models` | 返回 Anthropic 形状 `{data:[{type,id,display_name,created_at}],has_more,first_id,last_id}`，只暴露 route 名 |
| `POST /v1/messages` | 改写 `model` → route 对应真实模型；`output_config.effort` 强制为配置值；透传 SSE；上游补 `x-opencode-session`（优先用客户端的 `x-claude-code-session-id`，其次 `metadata.user_id.session_id`，否则随机 UUID）；上游认证用 `x-api-key` |
| `POST /v1/messages/count_tokens` | 透传（同样做模型改写） |
| 其它路径 | 404 + Anthropic 形状错误体 |
| 上游不可达 | 返回 Anthropic 形状 `{"type":"error",...}`，让桌面端显示可读错误而不是崩溃 |

### 6.1 第二笔调用（会话标题）与省流开关

实测：客户端每新建一个会话会额外发一笔"标题生成"请求，特征为
`tools=0`、`messages=1`、`output_config.format = json_schema{properties:{title}}`、
正文为 `<session>…</session>` + "Write the title in the predominant language…"。
该请求走 Haiku 槽（→ `deepseek-v4-flash`），**每个会话仅一次**（同一会话第二问实测不再触发）。

网关提供 `local_title_synthesis`（**默认 `false`**，按用户决定：保持与官方行为一致）：

- 命中上述特征时**本地直接应答**（返回 `{"title": "..."}`，SSE 与非流式两种形状），不发上游请求；
- 标题取会话首行、截断 24 字符；
- 未命中特征（例如带 tools 的正常请求）绝不拦截，保证安全。

预期收益：每个新会话省掉约 2.7k 输入 + 57 输出 token 的一次调用。

## 7. 验证标准

1. `curl http://127.0.0.1:<port>/v1/models` 返回 3 条 route。
2. `curl -N POST /v1/messages`（`model=claude-sonnet-4-5`、`stream=true`）返回 SSE，且 `message_start` 里 `model=deepseek-v4.1-flash`。
3. 桌面端：底部显示 `Gateway`、无凭据报错、模型选择器显示配置的 label。
4. 桌面端发一条真实消息 → 网关日志出现对应 POST 且上游 200，界面正常出字。
5. `--restore` 后桌面端回到安装前状态（`deploymentMode` 与 profile 原样），网关进程与 LaunchAgent 移除。
6. 开启 `local_title_synthesis` 后：新建会话只产生 1 次上游调用，且回答正常、标题非空。

## 7.1 实施与实测记录（2026-09-14）

| 验证项 | 结果 | 证据 |
|---|---|---|
| 假 HOME 安装全流程 | 通过 | `SKIP_AGENT` 降级为 pidfile；`--status` 自检 `200 / 3 routes` |
| 假 HOME `--restore` | 通过 | 安装前无 `Claude-3p` 时移除脚本创建的配置项，`vm_bundles` 等数据不动 |
| 真机安装 + LaunchAgent | 通过 | `launchctl print gui/501/com.bitqai.ccd-gateway` = running；8799 单进程监听 |
| 真机 `--status` | 通过 | `状态：运行中（LaunchAgent）自检 通过：200 / 3 routes` |
| 非流式调用 | 通过 | `claude-sonnet-4-5` → `model: deepseek-v4.1-flash`，返回 `OK`，usage 38/36 |
| 流式调用 | 通过 | SSE `message_start.message.model = deepseek-v4.1-flash`，`text_delta: OK` |
| opus / haiku 槽 | 通过 | `claude-opus-4-5` → `deepseek-v4-pro`；`claude-haiku-4-5` → `deepseek-v4-flash`（日志 200） |
| 标题本地应答 | 通过 | 真实格式 body（`<session>` 在前）返回 `{"title": "你好，简单介绍一下你自己"}`，日志 `-> local title` |
| 桌面端 UI | 通过 | 底部徽标 `Gateway`，模型选择器 `DeepSeek V4.1 Flash · max` |
| 桌面端真实消息 | 通过 | 用户发 `hi`：`18:15:50 claude-sonnet-4-5 -> deepseek-v4.1-flash status=200`，会话标题 `Casual greeting`（`18:15:45/59 haiku -> deepseek-v4-flash`），回答正常（不再是 `Hi Bill!`） |
| 真机 `--restore` 回归 | 通过 | `appliedId` 回 `Default`、profile `{}`、`deploymentMode` 移除、LaunchAgent 卸载、8799 释放、plist 删除、备份保留 |
| 还原后再安装 | 通过 | `Default` 条目保留 + 新增独立条目 `OpenCode Go (DeepSeek)`，`appliedId` 指向新条目 |

### 实施中发现并修复的缺陷

1. **双进程抢端口**：安装流程原先先用 pidfile 起网关、再由 LaunchAgent 起第二个，造成 8799 冲突与 KeepAlive 反复重启。
   现改为安装时只由 LaunchAgent 启动；`start/stop/status` 感知 LaunchAgent（`kickstart -k` 重载、`bootout` 停止）。
2. **备份/还原会误删 12G 数据**：原逻辑 `cp -R` 整个 `Claude-3p`（含 11G `vm_bundles`）并在还原时 `rmtree`。
   现只备份/还原 `configLibrary`、`claude_desktop_config.json`、`config.json`。
3. **假 HOME 污染真实 launchd**：新增 `SKIP_AGENT`，`HOME` 与真实用户目录不一致时跳过 LaunchAgent 并降级为 pidfile。
4. **`status` 误报未运行**：原先只认 pidfile；现 LaunchAgent 在载入即视为运行中并做 `GET /v1/models` 自检。
5. **`--restore` 用到旧代码**：还原前会同步仓库最新网关代码。
6. **非流式响应缺 `Content-Length`（体感"网关极慢"）**：上游以 chunked 分帧，网关把 `transfer-encoding` 当 hop-by-hop 丢弃后既没补长度也没标 chunked，客户端只能等连接关闭——实测同一请求经网关要 48～120 秒，而直连上游 1.6 秒。现在非流式响应读完 body 后显式写 `content-length`，流式统一 chunked，并丢弃上游的 `server`/`date` 避免重复头。修复后同一请求 2.7 秒。
7. **上游连接抖动直接 502**：本机 Clash Verge（TUN + fake-ip，`opencode.ai` → `198.18.0.77`）下约 1/3 连接在 TLS 阶段被中断（`SSLEOFError: UNEXPECTED_EOF_WHILE_READING`）。网关现在按 `upstream_retries`（默认 2）+ 退避重试，日志记录每次 attempt 的耗时与异常；连接超时与读超时分离（`upstream_connect_timeout` 30s / `upstream_read_timeout` 300s，替代原先一刀切的 600s）。

### 备份快照的注意事项（排错项）

安装脚本只新建自己的 profile 条目，**不覆盖** `Default`。但备份是"安装那一刻"的快照：
若安装前已有别的工具（如早期 spike）把 `Default` 条目改成了 gateway 配置，`--restore` 会忠实地还原到那个被污染的状态。
官方默认形态是 `Default` 的 profile 为 `{}`、`claude_desktop_config.json` 不含 `deploymentMode`。
本机已按此修正备份并复验还原 → 再安装，两步都通过。

## 8. 范围边界

**做**：本地网关、3P profile 事务化写入与回滚、LaunchAgent 常驻、key/端口/effort 管理、zh/en README。

**不做**：伪造 Anthropic 登录/凭据；修改系统代理；监听非 loopback 地址；
改写 Claude Desktop 应用包（app.asar）或签名；自动切换上游 provider（首期单上游）。

## 9. 风险与安全

| 风险 | 应对 |
|---|---|
| 桌面端升级后 3P schema 变化 | 安装前记录版本；profile 写入采用"仅改自己的条目"，可 `--restore` 回滚；README 写明兼容性核实点 |
| 真实 API Key 泄漏 | Key 只存网关配置（600）+ LaunchAgent 环境；Desktop profile 只放本地假 key；日志脱敏（不写 key/token/正文） |
| 网关崩溃导致桌面端不可用 | LaunchAgent KeepAlive；`--status` 自检；`--restore` 一键回官方模式 |
| 本地端口被其它进程占用 | 启动前端口探测，冲突时给出明确提示并支持 `--port` |
