# Claude Desktop × OpenCode Go (3P local gateway)

> This directory is the **Desktop** part of
> [ClaudeCode-opencode-setup](https://github.com/BitQAI/ClaudeCode-opencode-setup).
> For the terminal `claude` CLI and IDE extensions use `setup-claude-opencode.sh` in the repo root.

Run the official **Claude Desktop** app (macOS) on **OpenCode Go**'s DeepSeek models.
The Sonnet slot maps to `deepseek-v4.1-flash` with reasoning effort fixed to `max`.

```
Claude Desktop (third-party inference / Gateway mode)
        │  http://127.0.0.1:8799
        ▼
local gateway (Python 3 stdlib, loopback only)
        │  rewrite model + force effort=max + inject session header
        ▼
https://opencode.ai/zen/go/v1/messages
```

One command to install, one command to roll back. The upstream API key stays on this machine
(`~/.claude-desktop-opencode/config.json`, mode `600`) and is never written into Claude's profile.

---

## 1. Why a local forwarding layer is required

Measured on Claude Desktop `1.52386.6` (`/Applications/Claude.app`), macOS.

| Naive approach | Actual result | Verdict |
|---|---|---|
| Point Desktop straight at OpenCode Go with model `deepseek-v4.1-flash` | profile loads, but the entry is **stripped by model-name validation**; only Claude-style names remain | ✗ |
| Send `claude-sonnet-4-5` and hope the upstream accepts it | upstream returns `401 ModelError: Model ... is not supported` | ✗ |
| Let Desktop probe the third-party upstream for credentials | log shows `[custom-3p] Gateway /v1/models non-OK status 404`; UI says `The provider rejected your credentials` | ✗ |

Three constraints hold at once: **Desktop only accepts Anthropic-style model ids**, **the upstream only serves its
own ids**, and **Desktop blocks direct calls to third-party providers**. A translation layer in between is unavoidable.

### 1.1 No self-signed certificate needed

The commonly shared workaround claims you must wrap local HTTP in HTTPS and install a trusted root.
In practice the gateway URL check is **https, or http on loopback** (`cs({allowLoopbackHttp:true})` inside the app
bundle), so `http://127.0.0.1:8799` works as-is — **no certificate, no root trust, no system changes**.

### 1.2 Renaming a model is not enough

Desktop validates model ids before handing the profile to the inference layer, and the upstream rejects `claude-*`
ids outright. There is no config-only path that satisfies both, so this project takes the smallest possible step:
a loopback process that only translates.

---

## 2. Model mapping

| Desktop entry (Anthropic id) | Shown in picker | Upstream model | Effort |
|---|---|---|---|
| `claude-sonnet-4-5` | DeepSeek V4.1 Flash · max | `deepseek-v4.1-flash` | `max` |
| `claude-opus-4-5` | DeepSeek V4 Pro | `deepseek-v4-pro` | `max` |
| `claude-haiku-4-5` | DeepSeek V4 Flash | `deepseek-v4-flash` | `max` |

**The default (Sonnet) slot is `deepseek-v4.1-flash` + `max`**, working out of the box.
Change effort for all slots with `--effort`, or edit `routes` in `~/.claude-desktop-opencode/config.json`.

---

## 3. Install

Requirements: macOS, Claude Desktop installed, Python 3.8+.

```bash
git clone https://github.com/BitQAI/ClaudeCode-opencode-setup.git
cd ClaudeCode-opencode-setup/desktop
bash setup-desktop-opencode.sh
```

On first run you are prompted for an OpenCode Go API key (get one at <https://opencode.ai/zen>, `sk-…`).
The installer will:

1. back up the existing 3P config items (only `configLibrary/`, `claude_desktop_config.json`, `config.json` —
   VM/Cache data is never touched);
2. install the gateway into `~/.claude-desktop-opencode/gateway/` and write `config.json` (mode `600`);
3. write a 3P profile (`Claude-3p/configLibrary/<uuid>.json` + `_meta.json`) and set `deploymentMode = "3p"`;
4. install the LaunchAgent `com.bitqai.ccd-gateway` (`RunAtLoad` + `KeepAlive`);
5. restart Claude Desktop.

After install the app should show `Gateway` at the bottom and `DeepSeek V4.1 Flash · max` in the model picker.

### 3.1 Verify

```bash
bash setup-desktop-opencode.sh --status          # config + gateway self-check + profile + LaunchAgent
curl -s http://127.0.0.1:8799/v1/models          # expects 3 routes
python3 ~/.claude-desktop-opencode/gateway/ccd_gateway.py logs -n 40
```

Send any message from the app; the log shows:

```
POST /v1/messages claude-sonnet-4-5 -> deepseek-v4.1-flash effort=max status=200 2181ms
```

---

## 4. Commands

| Command | Purpose |
|---|---|
| `bash setup-desktop-opencode.sh` | install / redeploy |
| `bash setup-desktop-opencode.sh --status` | gateway, profile and LaunchAgent status (includes `GET /v1/models` self-check) |
| `bash setup-desktop-opencode.sh --key sk-xxxx` | update the OpenCode Go key (omit the value to be prompted) |
| `bash setup-desktop-opencode.sh --port 8899` | change the gateway port |
| `bash setup-desktop-opencode.sh --effort high` | change effort: `low\|medium\|high\|xhigh\|max` |
| `bash setup-desktop-opencode.sh --local-title on` | answer session-title requests locally (default `off`) |
| `bash setup-desktop-opencode.sh --restore` | roll back to the pre-install state and remove the LaunchAgent |
| `bash setup-desktop-opencode.sh --update` | pull the latest script from GitHub and redeploy |

The gateway can be driven directly too (`--config` accepts a custom path):

```bash
python3 ~/.claude-desktop-opencode/gateway/ccd_gateway.py status
python3 ~/.claude-desktop-opencode/gateway/ccd_gateway.py start|stop|logs
```

---

## 5. The second call per new session, and `--local-title`

Measured: for every **new session** Desktop sends one extra **title-generation** request besides the main answer.
Its fingerprint is `tools = []`, exactly one message, `output_config.format = json_schema{properties:{title}}`,
and a body shaped like `<session>…</session>` plus "Write the title in the predominant language…".
It goes through the Haiku slot (→ `deepseek-v4-flash`) and happens **once per session**; follow-up turns do not repeat it.

That is why the usage panel shows a pair of calls:

| Time | Model | Input | Output | What it is |
|---|---|---|---|---|
| 17:45 | `deepseek-v4.1-flash` | 35367 | 79 | main answer (Sonnet slot) |
| 17:45 | `deepseek-v4-flash` | 2745 | 57 | session title (Haiku slot) |

This is Desktop's own behaviour, not a duplicated gateway call. To drop it:

```bash
bash setup-desktop-opencode.sh --local-title on
```

The gateway recognises that fingerprint, answers locally with `{"title": "…"}` (first line, 24 chars max) and never
contacts the upstream — new sessions drop from 2 upstream calls to 1, with answers and titles intact.
It is **off by default** to stay identical to stock behaviour.

---

## 6. Files

| Path | Contents |
|---|---|
| `~/.claude-desktop-opencode/config.json` | listen address/port, upstream URL, key (`600`), routes, `local_title_synthesis` |
| `~/.claude-desktop-opencode/gateway/` | gateway code |
| `~/.claude-desktop-opencode/gateway.log` | redacted access log |
| `~/.claude-desktop-opencode/backup/` | pre-install 3P config backup + `manifest.txt` |
| `~/Library/LaunchAgents/com.bitqai.ccd-gateway.plist` | LaunchAgent definition |
| `~/Library/Application Support/Claude-3p/configLibrary/` | Desktop 3P profiles (only this script's own entry is managed) |

---

## 7. Troubleshooting

| Symptom | Check |
|---|---|
| `Gateway` badge missing | `--status` for the profile; fully quit (⌘Q) and reopen; otherwise `--restore` and reinstall |
| `The provider rejected your credentials` | invalid/expired key: `--key sk-…` (the gateway restarts automatically) |
| UI spins forever | `tail -f ~/.claude-desktop-opencode/gateway.log`; `upstream unreachable` means network/URL trouble |
| Port already in use | switch with `--port 8899`; the profile is updated to match |
| Config change not applied | the LaunchAgent is reloaded on change; if not, rerun the installer without arguments |
| After `--restore` Desktop still points at the old gateway | the backup is a snapshot taken **at install time**; if another tool had already rewritten the `Default` entry, that is what gets restored. Check `~/.claude-desktop-opencode/backup/Claude-3p/configLibrary/`: the stock `Default` profile is `{}` and `claude_desktop_config.json` has no `deploymentMode` |

Logs are always redacted: route id, rewritten model, status code and latency only — never keys, tokens or bodies.

---

## 8. Security and scope

- Binds `127.0.0.1` only; no system proxy; the app bundle/signature is never modified.
- The real upstream key lives only in `~/.claude-desktop-opencode/config.json` (`600`); the Desktop profile holds a
  placeholder key.
- Fully reversible: `--restore` puts the profile back and only touches the config items this script manages
  (`vm_bundles`, caches and other data are untouched).
- Out of scope: faking Anthropic credentials, switching upstream providers automatically.

Known boundary: if a Desktop upgrade changes the 3P schema or model-id validation, regenerate the profile by
rerunning the installer. The Desktop version is recorded in the backup manifest for traceability.

---

## 9. Relation to the terminal edition (Claude Code)

In the same repo the **terminal edition** is `setup-claude-opencode.sh` in the root (it writes
`~/.claude/settings.json`), while the **Desktop edition** is `setup-desktop-opencode.sh` in this directory
(it writes the Claude-3p profile plus a local gateway). They touch different files and never interfere:
install and roll back independently.

Desktop's built-in Chat / Projects use Anthropic server-side inference, are unaffected by this setup, and require
no re-login.

## License

MIT
