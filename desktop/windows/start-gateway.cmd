@echo off
rem Claude Desktop x OpenCode Go - local gateway launcher (auto-restart loop)
rem Copied here by setup.mjs. Keep this file CRLF and ASCII-only:
rem cmd.exe mis-parses non-ASCII bytes under a GBK console code page.
setlocal
set "DIR=%USERPROFILE%\.claude-desktop-opencode"
set "NODE="
for /f "delims=" %%f in ('where node 2^>nul') do if not defined NODE set "NODE=%%f"
if not defined NODE if exist "%LOCALAPPDATA%\pi-node\current\node.exe" set "NODE=%LOCALAPPDATA%\pi-node\current\node.exe"
if not defined NODE (
  echo [gateway] Node.js not found in PATH. Install Node 18+ and re-run: node setup.mjs>>"%DIR%\gateway-console.log"
  exit /b 1
)
:loop
if not exist "%DIR%\gateway.enabled" exit /b 0
rem Do NOT redirect into gateway.log: the gateway writes it itself (EBUSY on Windows).
"%NODE%" "%DIR%\gateway\ccd_gateway.mjs" run --config "%DIR%\config.json" >> "%DIR%\gateway-console.log" 2>&1
ping -n 6 127.0.0.1 >nul
goto loop
