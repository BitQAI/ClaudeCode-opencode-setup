# Plan：上下文窗口 / 自动压缩 / 上游超限错误

对应 Spec：`desktop/docs/superpowers/specs/2026-10-27-context-window-auto-compact.md`

---

## Task 1 — 配置层：`upstream_context_limit` 与 `[1m]` 路由派生

文件：`desktop/gateway/ccd_config.py`

- [ ] `DEFAULT_CONFIG` 增加 `"upstream_context_limit": 1_000_000`
- [ ] 新增常量 `LONG_CONTEXT_SUFFIX = "[1m]"` 与 `TOKENS_SUFFIX_LABEL = " · 1M"`
- [ ] 新增 `with_long_context_variants(routes) -> list[dict]`：为每条路由派生 `name + "[1m]"` 的副本
      （label 追加 ` · 1M`，其余字段原样），并保证幂等（已有 `[1m]` 名字不重复派生）
- [ ] 新增 `expanded_routes(config) -> list[dict]`：`with_long_context_variants(config["routes"])`
- [ ] `routes_by_name()` 改为遍历 `expanded_routes()`，使 `claude-*-[1m]` 直接命中，不再落 fallback + WARN

接口：

```python
def with_long_context_variants(routes: list[dict]) -> list[dict]  # 纯函数，输入输出等长或 2 倍
def expanded_routes(config: dict) -> list[dict]
def routes_by_name(config: dict) -> dict[str, dict]              # 语义扩展：含 1M 别名
```

## Task 2 — 网关：错误体日志 + `prompt is too long` 改写

文件：`desktop/gateway/ccd_upstream.py`、`desktop/gateway/ccd_gateway.py`

- [ ] `ccd_upstream.estimate_tokens(payload) -> int`（英文 4 字/token、CJK 1.5 字/token，`math.ceil`）
- [ ] `ccd_upstream.is_opaque_error_body(raw) -> bool`（空、≤200 字符、或以 `{"model"` 开头）
- [ ] `ccd_gateway.Gateway`：新增 `ERROR_BODY_LIMIT = 600` 与
      `respond_upstream_error(response, payload, path, route, requested, started)`
- [ ] `forward()`：`response.status >= 400` 时走 `respond_upstream_error()` 并 `return`，200 路径不变
- [ ] 改写条件：`status in (400, 413)` 且 `is_opaque_error_body` 且 `estimate > upstream_context_limit * 0.95`
      → `send_error_json(400, "invalid_request_error", f"prompt is too long: {estimate} tokens > {limit} maximum")`
- [ ] 其它错误：`send_error_json(status, "api_error", f"upstream {status}: {detail[:300]}")`

## Task 3 — 网关：`/v1/models` 暴露 1M 别名

文件：`desktop/gateway/ccd_gateway.py`、`desktop/gateway/ccd_desktop_profile.py`

- [ ] `do_GET` 的 `/v1/models` 改用 `ccd_config.expanded_routes(self.config)`
- [ ] `ccd_desktop_profile.profile_payload()` 的 `inferenceModels` 同样改用 `expanded_routes()`，
      使桌面端选择器直接出现 “DeepSeek V4.1 Flash · 1M · max”

## Task 4 — 终端脚本：写入自动压缩配置

文件：`setup-claude-opencode.sh`

- [ ] 新增可调参数 `AUTOCOMPACT_WINDOW_DEFAULT="${OPENCODE_AUTOCOMPACT_WINDOW:-950000}"`
- [ ] `apply_settings()`：新增第 9 个参数 `autocompact`，写入
      `env.CLAUDE_CODE_AUTO_COMPACT_WINDOW`、`env.CLAUDE_CODE_MAX_CONTEXT_TOKENS="1000000"`、
      `data.autoCompactEnabled=True`、`data.autoCompactWindow=int(autocompact)`
- [ ] `do_install()` 的完成摘要打印自动压缩窗口与阈值（`窗口 − 32000`）
- [ ] `--status` 输出增加这两项（若存在）

## Task 5 — 文档

文件：`desktop/README.md`、`desktop/README.en.md`、`README.md`、`README.en.md`

- [ ] `desktop/README*.md` §2 模型映射表补 `[1m]` 两行，并加一段“为什么要用 1M 别名”
- [ ] `desktop/README*.md` §7 排错表补三行：
      `Invalid request / The request couldn't be completed. {"model":...}`、每 ~16.8 万就 compact、
      日志里 `upstream_body=` 的用法
- [ ] 根 `README*.md`「写入后的 `~/.claude/settings.json`」小节补充自动压缩三项与其含义

## Task 6 — 验证

- [ ] `python3 -m py_compile desktop/gateway/*.py`
- [ ] `bash -n setup-claude-opencode.sh desktop/setup-desktop-opencode.sh`
- [ ] 单测式脚本（临时，不入库）：
      `expanded_routes` 3→6 且幂等、`routes_by_name` 命中 `[1m]`、`estimate_tokens` 量级、`is_opaque_error_body` 判定
- [ ] 冒烟：`Gateway` 起在本机端口，`GET /v1/models` 返回 6 条；伪造上游 400 空壳 → 断言返回 `prompt is too long`
- [ ] 每次 commit 前向用户确认（仓库 CLAUDE.md 要求）

## 风险与回滚

| 风险 | 缓解 |
|---|---|
| 桌面端不认识 `[1m]` 名字，选择器异常 | 别名只是**追加**，原 3 条路由保持不变；出问题在 `config.json` 删掉派生即可（无需改代码） |
| 误把参数错误改写成超限 | 仅在错误体是空壳且估算 > 95% 上限时改写；其余保留上游原文 |
| 950000 窗口压缩失败 | 阈值 918K，压缩请求 ≈ 历史 + 摘要 + 32K 输出 < 1M；仍失败时下调到 900000 |
| `CLAUDE_CODE_MAX_CONTEXT_TOKENS` 影响非预期模型 | 仅对非 `claude-*` 模型名生效（Claude Code 内部 `kE()` 门控） |
