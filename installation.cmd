@echo off
setlocal EnableExtensions

set "OPENCODE_VERSION=1.15.13"
set "OH_MY_OPENAGENT_VERSION=4.5.12"
set "OPENCODE_SCHEDULER_VERSION=1.3.0"
set "PLAYWRIGHT_MCP_VERSION=0.0.75"
set "TASK_HOUNDS_DEFAULT_MODEL=bailian-coding-plan/MiniMax-M2.5"

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "RUNTIME_DIR=%ROOT%\core\runtime"
set "OC_RUNTIME=%RUNTIME_DIR%\opencode_runtime"
set "OC_HOME=%RUNTIME_DIR%\opencode_home"
set "OC_XDG_CONFIG=%OC_HOME%\.config\opencode"
set "OC_DATA=%OC_HOME%\.local\share"
set "OC_CONFIG=%RUNTIME_DIR%\opencode_config"
set "OC_BIN=%OC_RUNTIME%\node_modules\opencode-ai\bin\opencode.exe"
set "SETTINGS_JSON=%RUNTIME_DIR%\settings.json"

echo.
echo Task Hounds installer
echo Root: %ROOT%
echo.

REM ── Step 1: Python deps ─────────────────────────────────────────
where python >nul 2>nul
if errorlevel 1 (
  echo ERROR: python was not found. Install Python 3.11+ first.
  exit /b 1
)

where pip >nul 2>nul
if errorlevel 1 (
  echo ERROR: pip was not found. Install pip first.
  exit /b 1
)

echo [1/4] Installing Python dependencies...
cd /d "%ROOT%"
call pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo !! pip install -r requirements.txt failed. Aborting.
  echo    Make sure you are in the correct venv / conda env.
  exit /b 1
)

echo.
echo [2/4] Installing task_hounds_api package (editable)...
call pip install -e .
if errorlevel 1 (
  echo.
  echo !! pip install -e . failed. Aborting.
  pause
  exit /b 1
)
echo.

REM ── Step 2: OpenCode runtime ─────────────────────────────────────
where npm >nul 2>nul
if errorlevel 1 (
  echo ERROR: npm was not found. Install Node.js LTS first.
  exit /b 1
)

where node >nul 2>nul
if errorlevel 1 (
  echo ERROR: node was not found. Install Node.js LTS first.
  exit /b 1
)

mkdir "%OC_RUNTIME%" 2>nul
mkdir "%OC_XDG_CONFIG%" 2>nul
mkdir "%OC_DATA%" 2>nul
mkdir "%OC_CONFIG%" 2>nul

echo [3/4] Installing opencode-ai@%OPENCODE_VERSION%...
call npm install --prefix "%OC_RUNTIME%" "opencode-ai@%OPENCODE_VERSION%" --no-audit --no-fund
if errorlevel 1 exit /b 1

echo Installing OpenCode plugins and MCP packages...
call npm install --prefix "%OC_CONFIG%" "oh-my-openagent@%OH_MY_OPENAGENT_VERSION%" "opencode-scheduler@%OPENCODE_SCHEDULER_VERSION%" "@playwright/mcp@%PLAYWRIGHT_MCP_VERSION%" --no-audit --no-fund
if errorlevel 1 exit /b 1

if not exist "%OC_BIN%" (
  echo ERROR: expected OpenCode binary was not found:
  echo %OC_BIN%
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop';" ^
  "$xdg='%OC_XDG_CONFIG%'; $cfg='%OC_CONFIG%';" ^
  "$config = [ordered]@{ '$schema'='https://opencode.ai/config.json'; plugin=@('oh-my-openagent@%OH_MY_OPENAGENT_VERSION%', 'opencode-scheduler@%OPENCODE_SCHEDULER_VERSION%'); model='%TASK_HOUNDS_DEFAULT_MODEL%'; mcp=[ordered]@{ playwright=[ordered]@{ type='local'; command=@('npx', '-y', '@playwright/mcp@%PLAYWRIGHT_MCP_VERSION%'); enabled=$true } } };" ^
  "$json = $config | ConvertTo-Json -Depth 20;" ^
  "Set-Content -LiteralPath (Join-Path $xdg 'opencode.jsonc') -Value $json -Encoding UTF8;" ^
  "Set-Content -LiteralPath (Join-Path $cfg 'opencode.jsonc') -Value $json -Encoding UTF8;"
if errorlevel 1 exit /b 1

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop';" ^
  "$settings='%SETTINGS_JSON%';" ^
  "if (Test-Path -LiteralPath $settings) { $data = Get-Content -LiteralPath $settings -Raw | ConvertFrom-Json } else { $data = [pscustomobject]@{} }" ^
  "$data | Add-Member -NotePropertyName opencode_bin -NotePropertyValue '%OC_BIN%' -Force;" ^
  "$data | Add-Member -NotePropertyName opencode_isolated_config -NotePropertyValue $true -Force;" ^
  "$data | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $settings -Encoding UTF8;"
if errorlevel 1 exit /b 1

echo.
echo [4/4] Verifying task_hounds_api...
python -c "import task_hounds_api; from task_hounds_api.api import create_app; print('  task_hounds_api import OK')"
if errorlevel 1 (
  echo.
  echo !! Verification failed. Check that pip install -e . succeeded.
  exit /b 1
)

echo.
echo ============================================
echo   Installation complete.
echo ============================================
echo.
echo   OpenCode binary:
echo   %OC_BIN%
echo.
echo   Config locations:
echo   %OC_XDG_CONFIG%\opencode.jsonc
echo   %OC_CONFIG%\opencode.jsonc
echo.
echo   To start the server:
echo     set PYTHONPATH=%ROOT%\core
echo     python -m task_hounds_api --port 8765
echo.
echo   Then open http://localhost:8765
echo.
echo   DEV MODE (hot-reload UI):
echo     Terminal 1: cd /d %ROOT%\core ^&^& set PYTHONPATH=%ROOT%\core ^&^& python -m task_hounds_api --port 8765
echo     Terminal 2: cd /d %ROOT%\ui\web ^&^& npm install ^&^& npm run dev
echo     Then open:  http://localhost:5173
echo ============================================
echo.

endlocal
