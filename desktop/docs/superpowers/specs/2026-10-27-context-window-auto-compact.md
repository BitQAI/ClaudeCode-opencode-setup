# Spec：上下文窗口 / 自动压缩 / 上游超限错误（2026-10-27）

## 问题背景

实测（Claude Desktop 3P + 本地网关 + OpenCode Go）暴露三个互相耦合的问题：

1. **会话写满 1M 后上游只回空壳 400**
   长会话（实测 915 条消息 / 4.86 MB 请求 / 100.9 万 prompt tokens）继续追加一轮即超出上游
   `deepseek-v4.1-flash` 的 **1,000,000 tokens** 上下文上限。OpenCode Go 的响应是
   `400 {"model":"deepseek-v4.1-flash"}`——没有任何 `error.message`，桌面端/Claude Code 无法识别
   `prompt is too long`，于是**不做反应式压缩**，界面上只显示
   `Invalid request / The request couldn't be completed.`，用户无从判断原因。
   对照实验证明与请求体大小无关（单块文本 6.1 MB ≈ 76.8 万 tokens 仍 200；8.6 MB ≈ 107 万 tokens 返回同样的空壳 400）。

2. **自动压缩频率过高（每 ~16.8 万 tokens 一次）**
   Claude Code 的自动压缩窗口会被**模型自身上下文窗口钳制**：`min(设置窗口, 模型窗口)`，
   阈值 ≈ `窗口 − 输出预留(32000)`。路由名 `claude-sonnet-4-5` 的模型窗口是 200K，
   于是阈值 = 168K，与实测 `compactMetadata.preTokens = 167249 / 168011 / 166786` 完全吻合。
   只调 `autoCompactWindow` 无效（被钳回 200K）。

3. **无法从日志定位**
   网关此前只记录状态码，不记录上游错误体，排查 1 只能靠猜。

## 方案概要

| 编号 | 方案 | 目的 |
|---|---|---|
| A | 网关记录上游错误体（截断 ≤600 字），并对"空壳 400 + 本地估算接近上限"改写成 Anthropic 风格 `prompt is too long: N tokens > M maximum` | 让 Claude Code 命中 `compactAfterRefusal` 走反应式压缩；同时把可读原因暴露给用户 |
| B | 网关对每条路由自动派生 `[1m]` 别名（`claude-sonnet-4-5[1m]` 等），`/v1/models` 与桌面端 profile 一并暴露 | 用 Claude Code 的 1M 别名把模型窗口抬到 1e6，从而允许 auto-compact 窗口设到 ~950K |
| C | 终端脚本写入 `CLAUDE_CODE_AUTO_COMPACT_WINDOW`（默认 950000）、`autoCompactWindow`、`autoCompactEnabled`；非 `claude-*` 模型名额外写 `CLAUDE_CODE_MAX_CONTEXT_TOKENS=1000000` | 阈值 ≈ 918K，接近 1M 才压缩，且压缩请求本身仍装得下（< 上游 1M） |

## 技术选型与关键约束

- **`[1m]` 别名是 Claude Code 官方机制**：`claude-sonnet-4-5[1m]` 在 CLI 内部命中 1M 分支，
  发请求时会把 `[1m]` 去掉并附加 `context-1m-2025-08-07` beta，因此**网关侧无需任何特殊处理**，
  只需保证路由表里有对应名字（否则会落到 fallback 并打 WARN）。
- **`CLAUDE_CODE_MAX_CONTEXT_TOKENS` 只对非 `claude-*` 模型名生效**，因此终端脚本
  （`ANTHROPIC_MODEL=deepseek-v4.1-flash`）用它，桌面端（`claude-*` 名）用 `[1m]` 别名，两条路各自成立。
- **窗口上限取 950000 而非 1000000**：压缩动作本身需要把整段历史 + 摘要指令 + 32000 输出装进一次请求，
  顶到 1M 会直接触发问题 1 的硬上限。
- 错误改写只在"上游错误体是空壳（≤200 字符或以 `{"model"` 开头）且本地估算 > 上限 × 0.95"时触发，
  避免把真正的参数错误误报成上下文超限；其余 4xx/5xx 仍按 `api_error` 透传（并附上游原文片段）。
- 本地 token 估算沿用网关已有口径（英文 ~4 字符/token，CJK ~1.5 字符/token），已知偏大约 15–20%，
  仅用于"接近上限"的判定与提示文案，不参与计费或路由。

## 范围边界

- 只动：`desktop/gateway/{ccd_config,ccd_gateway,ccd_upstream,ccd_desktop_profile}.py`、
  `setup-claude-opencode.sh`、`desktop/README{,.en}.md`、`README{,.en}.md`、本 Spec/Plan。
- 不动：上游协议、profile 结构、LaunchAgent、安装/还原流程。
- 不做：自动切换上游、按模型动态探测真实上下文（上游未提供该信息）。

## 验证标准

1. `python3 -m py_compile desktop/gateway/*.py` 通过；`bash -n` 校验两个安装脚本。
2. `ccd_config.expanded_routes()` 在只有 3 条基础路由时产出 6 条（每条路由派生 1 条 `[1m]`，幂等）；
   `routes_by_name()` 能命中 `claude-sonnet-4-5[1m]`，不再打 WARN。
3. `ccd_upstream.estimate_tokens()` 对 1 MB 纯文本返回量级 ≈ 25 万（与实测 13.1 万同数量级、偏保守）。
4. 网关对上游 4xx 的日志包含 `upstream_body=`；空壳 400 + 估算超限时返回
   `{"type":"error","error":{"type":"invalid_request_error","message":"prompt is too long: N tokens > 1000000 maximum"}}`。
5. 正常请求（200）行为不变：流式/非流式、`count_tokens`、`/v1/models` 全部照旧。
