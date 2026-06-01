@echo off
setlocal EnableExtensions

set "OPENCODE_VERSION=1.15.13"
set "OH_MY_OPENAGENT_VERSION=4.5.12"
set "OPENCODE_SCHEDULER_VERSION=1.3.0"
set "PLAYWRIGHT_MCP_VERSION=0.0.75"

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
echo Task Hounds OpenCode runtime installer
echo Root: %ROOT%
echo.

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

echo Installing opencode-ai@%OPENCODE_VERSION%...
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
  "$config = [ordered]@{ '$schema'='https://opencode.ai/config.json'; plugin=@('oh-my-openagent@%OH_MY_OPENAGENT_VERSION%', 'opencode-scheduler@%OPENCODE_SCHEDULER_VERSION%'); model='minimax-coding-plan/MiniMax-M2.7'; mcp=[ordered]@{ playwright=[ordered]@{ type='local'; command=@('npx', '-y', '@playwright/mcp@%PLAYWRIGHT_MCP_VERSION%'); enabled=$true } } };" ^
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
echo Installed Task Hounds OpenCode runtime.
echo OpenCode binary:
echo %OC_BIN%
echo.
echo Config locations:
echo %OC_XDG_CONFIG%\opencode.jsonc
echo %OC_CONFIG%\opencode.jsonc
echo.
echo To verify:
echo "%OC_BIN%" --version
echo.

endlocal
