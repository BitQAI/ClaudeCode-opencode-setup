# Claude Desktop × OpenCode Go — Windows edition (Node)

> This folder is the **Windows implementation** of the desktop setup in
> [ClaudeCode-opencode-setup](https://github.com/BitQAI/ClaudeCode-opencode-setup).
> macOS users want [`desktop/setup-desktop-opencode.sh`](../setup-desktop-opencode.sh) (bash + Python 3) instead.
> Both editions share the same routes, profile layout and log fields — only the runtime and the autostart mechanism differ.

```
Claude Desktop (third-party inference / Gateway mode)
        │  http://127.0.0.1:8799
        ▼
Local gateway (Node 18+, bound to 127.0.0.1)
        │  rewrite model + force effort=max + inject session header
        ▼
https://opencode.ai/zen/go/v1/messages
```

## 1. Requirements

| Item | Requirement |
|---|---|
| OS | Windows 10 / 11 |
| Node | **18+ (22 recommended)**, `node` on `PATH` |
| Claude Desktop | official Windows build. **Cowork requires the `.msix` (Store) build**; the legacy `.exe` (Squirrel) build has no Cowork and shows "Reinstall the desktop app to access Cowork…". The `Code` tab and the unified panel work either way |
| OpenCode Go key | `oc_sk-...` from <https://opencode.ai/zen> |

## 2. Install

```bat
git clone https://github.com/BitQAI/ClaudeCode-opencode-setup.git
cd ClaudeCode-opencode-setup\desktop\windows

node setup.mjs                 :: install / update (prompts for the key when missing)
node setup.mjs --key           :: show / change the key (restarts the gateway)
node setup.mjs --status        :: config + profile + scheduled task + self-check
node setup.mjs --restore       :: rollback (task, Run key, profile, gateway)
```

What the installer does:

1. backs up the `%LOCALAPPDATA%\Claude-3p` config items (`configLibrary`, `claude_desktop_config.json`,
   `config.json`) into `%USERPROFILE%\.claude-desktop-opencode\backup\` — config only, never the multi-GB `vm_bundles`/caches;
2. installs the gateway into `%USERPROFILE%\.claude-desktop-opencode\gateway\` and self-installs `setup.mjs`;
3. writes the 3P profile: `inferenceProvider=gateway`, `inferenceGatewayBaseUrl=http://127.0.0.1:8799`,
   the model list (including the `[1m]` 1M entries), `desktopHome=no-vm`, `chatTabEnabled=true`, `coworkTabEnabled=false`;
4. registers autostart: scheduled task `ClaudeDesktopOpenCodeGateway` (on logon) plus a `HKCU\...\Run` fallback;
5. starts the gateway in `supervise` mode (no console window; restarts the child 3s after it dies);
6. restarts Claude Desktop.

> **Never** write `HKCU\Software\Policies\Claude`: once that key exists Desktop treats the machine as "managed"
> and locks itself in the admin flow (the window won't open). Everything the setup needs lives in the profile;
> the script only writes policies when `CCD_WRITE_POLICY=1`.

## 3. File locations (differences from macOS)

| Purpose | Windows | macOS |
|---|---|---|
| Install dir | `%USERPROFILE%\.claude-desktop-opencode\` | `~/.claude-desktop-opencode/` |
| 3P profile | `%LOCALAPPDATA%\Claude-3p\configLibrary\<uuid>.json` + `_meta.json` | `~/Library/Application Support/Claude-3p/configLibrary/...` |
| Deployment switch | `%LOCALAPPDATA%\Claude-3p\claude_desktop_config.json` → `{"deploymentMode":"3p"}` | same structure |
| Desktop log | `%LOCALAPPDATA%\Claude-3p\logs\main.log` | in-app logs |
| Gateway log | `%USERPROFILE%\.claude-desktop-opencode\gateway.log` (`gateway-console.log` = stdout) | same |
| Autostart | scheduled task + `HKCU\...\Run`, supervisor `ccd_gateway.mjs supervise` | LaunchAgent (`KeepAlive`) |
| Runtime | Node (`gateway/*.mjs`) | `python3` (`gateway/*.py`) |
| Terminal / built-in Claude Code settings | `%USERPROFILE%\.claude\settings.json` | `~/.claude/settings.json` |

## 4. Model mapping

| Desktop entry | Shown in picker | Upstream model | Effort |
|---|---|---|---|
| `claude-sonnet-4-5` | DeepSeek V4.1 Flash · max | `deepseek-v4.1-flash` | `max` |
| `claude-opus-4-5` | DeepSeek V4 Pro | `deepseek-v4-pro` | `max` |
| `claude-haiku-4-5` | DeepSeek V4 Flash | `deepseek-v4-flash` | `max` |
| `claude-sonnet-4-5[1m]` | DeepSeek V4.1 Flash · max · 1M | `deepseek-v4.1-flash` | `max` |
| `claude-opus-4-5[1m]` | DeepSeek V4 Pro · 1M | `deepseek-v4-pro` | `max` |
| `claude-haiku-4-5[1m]` | DeepSeek V4 Flash · 1M | `deepseek-v4-flash` | `max` |

`[1m]` is Claude Code's built-in long-context alias; the gateway derives it for every route and the CLI strips it
before the request leaves the machine. **Pick a `· 1M` entry for long sessions** — without it the window is 200K and
auto-compaction fires every **~168K tokens** (see `../README.en.md` §7).

## 5. Troubleshooting

| Symptom | Check |
|---|---|
| No `Gateway` badge | `node setup.mjs --status`; fully quit and reopen Desktop; otherwise `--restore` and reinstall |
| `Gateway was unreachable` / everything 502 | gateway not running: check `gateway-console.log` for "Node.js not found"; `node setup.mjs --status` |
| `The provider rejected your credentials` | key invalid/expired: `node setup.mjs --key oc_sk-...` (restarts the gateway) |
| `Invalid request / The request couldn't be completed.` with `{"model":"deepseek-v4.1-flash"}` | the prompt exceeded the **1,000,000 token** upstream limit. OpenCode Go answers with a bare 400 that has no `error.message`; small requests with the same model and `/v1/models` / `count_tokens` are fine, so it is not the model id or the gateway. `/compact` or start a new session |
| Long sessions compact every ~168K tokens | the auto-compact window is clamped to the model window (200K for `claude-sonnet-4-5`, threshold = 200000 − 32000 = 168K). Switch to a `· 1M` entry; for the terminal CLI also set `CLAUDE_CODE_AUTO_COMPACT_WINDOW` (see the root README) |
| Which error is it really? | `grep upstream_body "%USERPROFILE%\.claude-desktop-opencode\gateway.log"` — every 4xx/5xx upstream body is logged (600 chars). `rewrite upstream 400 -> prompt_too_long` means it was translated into `prompt is too long: N tokens > 1000000 maximum`, which Claude Code can act on by compacting |
| Config change not applied | the gateway restarts on config change; Desktop must be fully restarted (it caches the profile) |
| Change the port | `node setup.mjs --port 8899` (profile and launcher follow) |

## 6. Known boundaries of the Windows edition

- No `--update` (the macOS script re-downloads itself via `git ls-remote`): `git pull`, then rerun `node setup.mjs`.
- Cowork (a Windows-only tab) needs the `.msix` build; the profile ships `coworkTabEnabled=false`.
- After a Desktop upgrade that changes the 3P schema or model-id validation, rerun `node setup.mjs` to regenerate the
  profile (the backup manifest records the Desktop version).
- Logs stay redacted: route id, rewritten model, status, latency and an **upstream error-body snippet** — never keys or
  request bodies.
