# Claude Code × OpenCode Go (deepseek-v4.1-flash, native multimodal) — Setup

[中文](/README.md) | [English](/README.en.md)

Configure **Claude Code** to run on models from an **OpenCode Go** subscription. Default main model is `deepseek-v4.1-flash` (native text + image). Opus / Sonnet / Haiku model slots map to the strong / mid / fast models inside the Go plan.

Sister project of [codex-opencode-setup](https://github.com/BitQAI/codex-opencode-setup): that one configures Codex, this one configures Claude Code — both share the same OpenCode Go API key.

This repo ships two parts:

| Path | Target | Writes to |
|---|---|---|
| root (`setup-claude-opencode.sh`) | terminal `claude` CLI, IDE extensions, Claude Desktop's built-in Claude Code sessions | `~/.claude/settings.json` |
| [`desktop/`](/desktop) (`setup-desktop-opencode.sh`) | Claude Desktop **Chat / Cowork** (third-party inference via a 3P local gateway) | `Claude-3p` profile + `~/.claude-desktop-opencode/` |

Also included: [`CLAUDE.md`](/CLAUDE.md) — the engineering guidelines used in this repo (Claude Code picks it up
automatically when working here), kept identical to `~/.claude/CLAUDE.md`.
Note it only governs **Claude Code**: Desktop's Chat / Cowork never read a filesystem `CLAUDE.md` and instead use
**Instructions for Claude** in Settings (see [desktop/README.en.md 6.1](/desktop/README.en.md) for where that is stored).

---

## 1. Architecture

```
Claude Code (CLI / desktop)
  │  ~/.claude/settings.json → env.ANTHROPIC_BASE_URL = https://opencode.ai/zen/go
  │  Native Anthropic Messages protocol — no middleware
  ▼
https://opencode.ai/zen/go/v1/messages   (OpenCode Go, Anthropic-compatible endpoint)
  ├── deepseek-v4.1-flash   main model (native text + image)
  ├── deepseek-v4-pro       Opus slot
  ├── deepseek-v4-flash     Haiku slot (fast / cheap background work)
  └── glm-5.2 / kimi-k3 / qwen3.8-max / minimax-m3 … (other Go models)
```

Design notes:

- **Zero middleware** — Claude Code already speaks the Anthropic protocol and OpenCode Go serves `/v1/messages` natively. No router/proxy process needed.
- **Only the `env` block of `settings.json` is touched** — hooks, permissions, MCP servers and other keys are preserved (merge, not overwrite).
- **One-command rollback** — `settings.json` and `~/.claude.json` are backed up before install; `--restore` puts them back.

---

## 2. Quick start

Requirements:

1. Claude Code installed and started at least once (`~/.claude/` and `~/.claude.json` exist), `claude --version` prints a version.
2. An **OpenCode Go** API key (`sk-…`, from https://opencode.ai/zen).
3. **Python 3.8+** (used to merge JSON safely).

```bash
# One-liner from GitHub
bash <(curl -fsSL https://raw.githubusercontent.com/BitQAI/ClaudeCode-opencode-setup/main/setup-claude-opencode.sh)

# Or from a local clone
cd ClaudeCode-opencode-setup && bash setup-claude-opencode.sh
```

First run asks for the API key. Re-running backs up the current config again and re-applies.

What the script does:

1. Backs up `~/.claude/settings.json` and `~/.claude.json` into `~/.claude/backup-claude-opencode/` (plus a `manifest.txt` describing the pre-install state).
2. Validates the key with a real request to `/v1/messages` (expects HTTP 200).
3. Stores the key in `~/.claude/claude-opencode-setup/.env` (mode `600`).
4. **Merges** the `env` block into `~/.claude/settings.json` and removes any leftover `ANTHROPIC_AUTH_TOKEN`.
5. Registers the key in `~/.claude.json` → `customApiKeyResponses.approved` (last 20 chars) so the interactive "Use this API key?" prompt is skipped.
6. Installs itself into `~/.claude/claude-opencode-setup/` for later `--key` / `--status` / `--update`.

Resulting `~/.claude/settings.json`:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://opencode.ai/zen/go",
    "ANTHROPIC_API_KEY": "sk-your-opencode-go-key",
    "ANTHROPIC_MODEL": "deepseek-v4.1-flash",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "deepseek-v4-pro",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "deepseek-v4.1-flash",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-v4-flash",
    "CLAUDE_CODE_SUBAGENT_MODEL": "deepseek-v4.1-flash",
    "CLAUDE_CODE_EFFORT_LEVEL": "max"
  }
}
```

Restart Claude Code (open a new terminal) and run `claude` in any project.

### Model slot mapping (default slot = Sonnet)

| Claude Code slot | Actual model | Notes |
|---|---|---|
| **Default / Sonnet** | `deepseek-v4.1-flash` | primary slot, **effort = max**, native multimodal (text + image) |
| Opus (`/model opus`) | `deepseek-v4-pro` | stronger reasoning slot |
| Haiku | `deepseek-v4-flash` | background work (titles, probes, summaries), fast and cheap |
| Subagents | `deepseek-v4.1-flash` | via `CLAUDE_CODE_SUBAGENT_MODEL` |

`CLAUDE_CODE_EFFORT_LEVEL=max` drives the effort level (it overrides the `effortLevel` setting, whose schema only allows `low|medium|high|xhigh`). Captured requests confirm it is sent to the API and accepted by OpenCode Go:

```json
{"model":"deepseek-v4.1-flash","thinking":{"type":"adaptive"},"output_config":{"effort":"max"}, ...}
```

Change it with `--effort low|medium|high|xhigh|max`.

### Claude Desktop's built-in Claude Code works the same way

1. Its sessions are created with `settingSources: ['user','project','local']` — i.e. it reads `~/.claude/settings.json`;
2. `settings.json`'s `env` **outranks process environment variables** (verified: junk `ANTHROPIC_BASE_URL`/`ANTHROPIC_API_KEY` in the process env still routed to opencode successfully), so the provider env the desktop injects cannot override this config;
3. The desktop's own "environment variables" page (`ccd-environment-config`, encrypted via the OS keychain) therefore does **not** need to be filled in;
4. Fully quit and relaunch Claude.app after changing the config.

> Note the distinction: the above covers the **Claude Code sessions** inside Desktop (the `Code` tab, which reads
> `~/.claude/settings.json`). Desktop's **Chat / Cowork** runs on Anthropic server-side inference and needs the
> 3P local gateway in [`desktop/`](/desktop). The two never interfere and can be installed independently.

---

## 3. Two gotchas (verified empirically)

**Use `ANTHROPIC_API_KEY`, not `ANTHROPIC_AUTH_TOKEN`.**
The [DeepSeek docs](https://api-docs.deepseek.com/zh-cn/quick_start/agent_integrations/claude_code) use `ANTHROPIC_AUTH_TOKEN` because `api.deepseek.com/anthropic` accepts `Authorization: Bearer`. OpenCode Go's `/v1/messages` only accepts `x-api-key`; Bearer yields `401 AuthError: Missing API key`. Claude Code picks the header from the env var: `ANTHROPIC_API_KEY` → `x-api-key`, `ANTHROPIC_AUTH_TOKEN` → `Authorization: Bearer`. This repo sets the former and strips the latter.

**No custom session header needed.**
OpenCode Go needs a stable session id per conversation (otherwise `400 MissingSessionID`), and the official [validated clients](https://opencode.ai/docs/go/#validated-clients) list states Claude Code is recognized by its native session header. Packet capture confirms Claude Code 2.1.x always sends `x-claude-code-session-id: <uuid>` — so no proxy or `ANTHROPIC_CUSTOM_HEADERS` wrapper is required.

---

## 4. Manual setup (without the script)

```bash
python3 - <<'PY'
import json, os, pathlib
p = pathlib.Path.home() / ".claude" / "settings.json"
data = json.loads(p.read_text()) if p.exists() else {}
data.setdefault("env", {}).update({
    "ANTHROPIC_BASE_URL": "https://opencode.ai/zen/go",
    "ANTHROPIC_API_KEY": "sk-your-opencode-go-key",
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

# Approve the key (last 20 chars) to skip the confirmation prompt
python3 - <<'PY'
import json, os, pathlib
key = "sk-your-opencode-go-key"
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

---

## 5. Commands

```bash
SCRIPT=~/.claude/claude-opencode-setup/setup-claude-opencode.sh

bash $SCRIPT            # install / re-apply
bash $SCRIPT --key      # view or change the API key (interactive)
bash $SCRIPT --key sk-xxxx
bash $SCRIPT --effort max  # reasoning effort (default max: low|medium|high|xhigh|max)
bash $SCRIPT --status   # show config + endpoint connectivity check
bash $SCRIPT --update   # fetch the latest script from GitHub and redeploy
bash $SCRIPT --restore  # roll back to the pre-install state
```

---

## 6. Verification

```bash
# Endpoint (expect HTTP 200)
curl -s -o /dev/null -w '%{http_code}\n' https://opencode.ai/zen/go/v1/messages \
  -H "x-api-key: $KEY" -H "anthropic-version: 2023-06-01" -H "content-type: application/json" \
  -H "x-claude-code-session-id: $(uuidgen)" \
  -d '{"model":"deepseek-v4.1-flash","max_tokens":16,"messages":[{"role":"user","content":"ping"}]}'

# Claude Code end to end
claude -p "Reply with exactly: PONG" --output-format json
claude -p "Use the Bash tool to run exactly: echo TOOL_OK" --allowedTools Bash --output-format json
```

### Verified on 2026-09-14 (macOS, Claude Code 2.1.197, OpenCode Go)

| Check | Result |
|---|---|
| `POST /v1/messages` with `x-api-key` + session header | `200`, Anthropic-format message |
| Same request with `Authorization: Bearer` instead | `401 AuthError: Missing API key` |
| Request without any session header | `400 MissingSessionID` (Claude Code always sends one) |
| `claude -p ...` using only `settings.json` | `is_error:false`, `result:"PONG"` on `deepseek-v4.1-flash` |
| Bash tool call | 2 turns, `result:"OPENCODE_TOOL_OK"` |
| Haiku slot mapping | auxiliary calls hit `deepseek-v4-flash` |
| Image input | red 64×64 PNG → `"Red"` |
| Model availability | `deepseek-v4.1-flash` / `deepseek-v4-flash` / `deepseek-v4-pro` all `200` |
| Default slot = Sonnet | session default `model: sonnet` → actual request model `deepseek-v4.1-flash` |
| Effort max | `CLAUDE_CODE_EFFORT_LEVEL=max` → request carries `output_config.effort="max"` + `thinking: adaptive`, `200` with no warning |
| settings beats process env | junk `ANTHROPIC_BASE_URL`/`API_KEY` + valid settings → still routed to opencode (`is_error:false`) |
| Desktop session setting source | `Claude.app` uses `settingSources=['user','project','local']` |

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `401 Missing API key` | `ANTHROPIC_AUTH_TOKEN` in use (sends Bearer), or key is not an OpenCode Go key | Make sure `settings.json` sets `ANTHROPIC_API_KEY`; remove `ANTHROPIC_AUTH_TOKEN`; re-run `--key` to validate |
| `400 MissingSessionID` | Request carries no session header | Use Claude Code itself; for manual curl add `x-claude-code-session-id`; any custom proxy must forward the header |
| Interactive prompt about the API key | Key not in `customApiKeyResponses.approved` | Re-run the installer (it registers the last 20 chars) |
| Auth failures although curl works | Stale `ANTHROPIC_*` exports in the shell profile | `env \| grep ANTHROPIC` and clean them up |
| `403 error code: 1010` | Cloudflare blocking default Python UA | Use `curl` or a normal user agent for manual tests |
| Running out of context | Go models provide a 200K window | Let Claude Code auto-compact, or `/compact` / start a new session |
| Key change has no effect | Config is read at startup | Restart Claude Code |

---

## 8. Security

- The key is stored in `~/.claude/claude-opencode-setup/.env` (mode `600`) and `~/.claude/settings.json` (mode `600`). Never commit either file.
- `~/.claude.json` is rewritten only for `customApiKeyResponses`; everything else is preserved, and the original is backed up under `~/.claude/backup-claude-opencode/claude.json`.
- The script itself never embeds your key — it is supplied at runtime.

---

## 9. Scope and limitations

- Only the bash installer is shipped (works on macOS, Linux and Windows Git Bash — it handles Windows paths and the `python` command name). A PowerShell version is intentionally omitted: there is no Windows/PowerShell environment available to verify it, and unverified code is not shipped here.
- Default mapping targets the DeepSeek models inside OpenCode Go. Switch to `glm-5.2`, `kimi-k3`, etc. by editing the model names in `settings.json` (`curl $BASE_URL/v1/models` lists everything available).

---

## 10. Desktop edition (Claude Desktop Chat / Cowork)

Desktop's Chat / Projects / Cowork run on **Anthropic server-side inference** and never read `~/.claude/settings.json`,
so the CLI setup above cannot reach them. Since `1.40609` Desktop ships an official "third-party inference" (3P) mode,
but three constraints were measured: Desktop **only accepts Anthropic-style model ids**, OpenCode Go **rejects
`claude-*` names**, and Desktop **blocks direct calls to third-party providers**.

[`desktop/`](/desktop) therefore ships a small loopback gateway: Desktop still sees `claude-sonnet-4-5` /
`claude-opus-4-5` / `claude-haiku-4-5`, the gateway rewrites them to `deepseek-v4.1-flash` / `deepseek-v4-pro` /
`deepseek-v4-flash`, forces `effort=max`, and forwards to OpenCode Go. **The Sonnet slot defaults to
`deepseek-v4.1-flash` + `max`.**

```bash
git clone https://github.com/BitQAI/ClaudeCode-opencode-setup.git
cd ClaudeCode-opencode-setup/desktop
bash setup-desktop-opencode.sh           # install (prompts for the key on first run)
bash setup-desktop-opencode.sh --status  # status
bash setup-desktop-opencode.sh --restore # roll back
```

Details, measurements and troubleshooting: [desktop/README.en.md](/desktop/README.en.md).

- The gateway binds `127.0.0.1` only; the key lives in `~/.claude-desktop-opencode/config.json` (`600`) and the
  Desktop profile carries a placeholder key.
- **No self-signed certificate needed**: Desktop accepts plain http on loopback (measured `allowLoopbackHttp: true`),
  one system layer less than the commonly shared "wrap it in HTTPS" recipe.
- Each new session sends one extra "title generation" request (Haiku slot); stock behaviour is preserved by default,
  and `--local-title on` lets the gateway answer it locally.
