@echo off
setlocal EnableExtensions EnableDelayedExpansion
title Start Task Hounds Safe
color 0A

for %%I in ("%~dp0.") do set "ROOT=%%~fI"
set "RUNTIME_DIR=%APPDATA%\task-hounds\runtime"
set "DATA_DIR=%APPDATA%\task-hounds\data"
set "POWER_TEAMS_RUNTIME_DIR=%RUNTIME_DIR%"
set "POWER_TEAMS_DB=%DATA_DIR%\power_teams.db"
set "PYTHONPATH=%ROOT%\core"
set "TASK_HOUNDS_OPENCODE_PORT=18765"
set "TASK_HOUNDS_PORT_CONFLICT=quit"
set "TASK_HOUNDS_OPENCODE_EMIT_LOG=%ROOT%\core\runtime\logs\opencode\emit.log"
set "TASK_HOUNDS_OPENCODE_SERVE_STATUS_LOG=%ROOT%\core\runtime\logs\opencode\opencode_serve_status.log"
set "TASK_HOUNDS_MANAGED_OPENCODE=%ROOT%\core\runtime\opencode_runtime\node_modules\opencode-ai\bin\opencode.exe"

echo ============================================
echo   Start Task Hounds Safe
echo ============================================
echo.
echo ROOT:      %ROOT%
echo RUNTIME:   %POWER_TEAMS_RUNTIME_DIR%
echo DB:        %POWER_TEAMS_DB%
echo LOGS:      %ROOT%\core\runtime\logs
echo API:       http://localhost:8766
echo OpenCode:  127.0.0.1:%TASK_HOUNDS_OPENCODE_PORT%
echo.

if not exist "%ROOT%\core\task_hounds_api" (
    echo ERROR: Cannot find task_hounds_api under:
    echo    %ROOT%\core\task_hounds_api
    pause
    exit /b 1
)

if not exist "%RUNTIME_DIR%" mkdir "%RUNTIME_DIR%"
if not exist "%DATA_DIR%" mkdir "%DATA_DIR%"
if not exist "%ROOT%\core\runtime\logs\opencode" mkdir "%ROOT%\core\runtime\logs\opencode"
if not exist "%ROOT%\core\runtime\logs\server-start" mkdir "%ROOT%\core\runtime\logs\server-start"

echo [0/7] Checking port 8766...
set "PORT_BUSY="
for /f %%P in ('powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; if ($c) { $c.OwningProcess }"') do (
    set "PORT_BUSY=%%P"
)
if defined PORT_BUSY (
    echo.
    echo ERROR: Port 8766 is already in use by PID !PORT_BUSY!.
    echo    This script will NOT start on another port, because that causes stale-server confusion.
    echo.
    echo    Inspecting process !PORT_BUSY!...
    echo.
    tasklist /FI "PID eq !PORT_BUSY!" 2>nul
    echo.
    echo    Process command line and child process tree:
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$procs=Get-CimInstance Win32_Process; function ShowTree([int]$processId,[int]$level){ $p=$procs | Where-Object { $_.ProcessId -eq $processId } | Select-Object -First 1; if ($null -eq $p) { return }; $indent='  ' * $level; $cmd=($p.CommandLine -replace '\s+', ' ').Trim(); Write-Host ($indent + '[Process]'); Write-Host ($indent + '  PID : ' + $p.ProcessId); Write-Host ($indent + '  PPID: ' + $p.ParentProcessId); Write-Host ($indent + '  NAME: ' + $p.Name); Write-Host ($indent + '  CMD : ' + $cmd); Write-Host ''; $procs | Where-Object { $_.ParentProcessId -eq $processId } | ForEach-Object { ShowTree ([int]$_.ProcessId) ($level + 1) } }; ShowTree !PORT_BUSY! 0"
    echo.
    set /p KILL_CHOICE="Do you want to kill this process? (Y/N): "
    if /i "!KILL_CHOICE!"=="Y" (
        echo Killing PID !PORT_BUSY!...
        taskkill /PID !PORT_BUSY! /T /F
        if !ERRORLEVEL! equ 0 (
            echo Successfully killed PID !PORT_BUSY!.
            echo Waiting for port to be released...
            timeout /t 2 /nobreak >nul
            set "PORT_BUSY="
            for /f %%P in ('powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; if ($c) { $c.OwningProcess }"') do (
                set "PORT_BUSY=%%P"
            )
            if defined PORT_BUSY (
                echo.
                echo ERROR: Failed to release port 8766. PID !PORT_BUSY! is still using it.
                echo    Please stop it manually and run this script again.
                echo.
                pause
                exit /b 1
            )
            echo Port 8766 is now free. Continuing...
        ) else (
            echo.
            echo ERROR: Failed to kill PID !PORT_BUSY!. It may require elevated permissions.
            echo    Please stop it manually and run this script again.
            echo.
            pause
            exit /b 1
        )
    ) else (
        echo.
        echo Cancelled. Please stop the process manually and run this script again.
        echo.
        pause
        exit /b 1
    )
)
echo Port 8766 is free.
echo.

echo [1/7] Checking managed OpenCode runtime...
if not exist "%TASK_HOUNDS_MANAGED_OPENCODE%" (
    echo Managed OpenCode was not found. Starting installation.cmd...
    echo.
    call "%ROOT%\installation.cmd"
    if !ERRORLEVEL! neq 0 (
        echo.
        echo ERROR: installation.cmd failed. Aborting.
        pause
        exit /b 1
    )
    if not exist "%TASK_HOUNDS_MANAGED_OPENCODE%" (
        echo.
        echo ERROR: installation.cmd completed but OpenCode was not found:
        echo    %TASK_HOUNDS_MANAGED_OPENCODE%
        pause
        exit /b 1
    )
    echo.
    echo Managed OpenCode installation completed.
) else (
    echo Managed OpenCode is installed.
)
echo.

echo [2/7] Installing Python dependencies...
cd /d "%ROOT%"
call pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo.
    echo ERROR: pip install failed. Aborting.
    pause
    exit /b 1
)
echo.

echo [3/7] Installing task_hounds_api package (editable)...
call pip install -e .
if %ERRORLEVEL% neq 0 (
    echo.
    echo ERROR: pip install -e . failed. Aborting.
    pause
    exit /b 1
)
echo.

echo [4/7] Installing frontend dependencies...
cd /d "%ROOT%\ui\web"
call npm install
if %ERRORLEVEL% neq 0 (
    echo.
    echo ERROR: npm install failed. Aborting.
    pause
    exit /b 1
)
echo.

echo [5/7] Building frontend...
call npm run build
if %ERRORLEVEL% neq 0 (
    echo.
    echo ERROR: npm run build failed. Aborting.
    pause
    exit /b 1
)
echo.

echo [6/7] Preflight: verify MiniMax key is available from env or .env...
cd /d "%ROOT%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$key='OPENCODE_API_KEY_MINIMAX'; if ([Environment]::GetEnvironmentVariable($key)) { Write-Host ($key + '= present (process env)'); exit 0 }; $paths=@((Join-Path $env:POWER_TEAMS_RUNTIME_DIR '.env'),(Join-Path $env:ROOT '.env')); foreach ($path in $paths) { if (-not (Test-Path -LiteralPath $path)) { continue }; foreach ($line in Get-Content -LiteralPath $path) { if ($line -match ('^\s*' + [regex]::Escape($key) + '\s*=\s*(.+?)\s*$')) { $value=$Matches[1].Trim().Trim('\"').Trim([char]39); if ($value) { Write-Host ($key + '= present (' + $path + ')'); exit 0 } } } }; Write-Host ($key + '= MISSING'); Write-Host 'Checked:'; foreach ($path in $paths) { Write-Host ('  - ' + $path) }; exit 2"
if %ERRORLEVEL% neq 0 (
    echo.
    echo ERROR: OPENCODE_API_KEY_MINIMAX was not found in process env or .env files.
    echo    Check:
    echo      %POWER_TEAMS_RUNTIME_DIR%\.env
    echo    or:
    echo      %ROOT%\.env
    pause
    exit /b 1
)
echo.

echo [7/7] Starting Task Hounds supervisor on fixed API port 8766...
cd /d "%ROOT%\core"
start "Task Hounds - Supervisor (8766)" /min cmd /k "set POWER_TEAMS_RUNTIME_DIR=%POWER_TEAMS_RUNTIME_DIR%&& set POWER_TEAMS_DB=%POWER_TEAMS_DB%&& set PYTHONPATH=%PYTHONPATH%&& set TASK_HOUNDS_OPENCODE_PORT=%TASK_HOUNDS_OPENCODE_PORT%&& set TASK_HOUNDS_PORT_CONFLICT=quit&& set TASK_HOUNDS_OPENCODE_EMIT_LOG=%TASK_HOUNDS_OPENCODE_EMIT_LOG%&& set TASK_HOUNDS_OPENCODE_SERVE_STATUS_LOG=%TASK_HOUNDS_OPENCODE_SERVE_STATUS_LOG%&& python -m task_hounds_api.supervisor --host 127.0.0.1 --port 8766 --reload"
echo Waiting for FastAPI health endpoint...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$deadline=(Get-Date).AddSeconds(60); do { try { $r=Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8766/api/health' -TimeoutSec 2; if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) { exit 0 } } catch {}; Start-Sleep -Milliseconds 750 } while ((Get-Date) -lt $deadline); exit 1"
if %ERRORLEVEL% neq 0 (
    echo.
    echo ERROR: FastAPI did not become ready on http://127.0.0.1:8766/api/health.
    echo    Browser will not be opened because the service cannot be reached yet.
    echo    Check logs:
    echo      %ROOT%\core\runtime\logs\server-start
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Started
echo   Dashboard:  http://localhost:8766
echo   Swagger:    http://localhost:8766/docs
echo ============================================
echo.
echo If the page still shows the old X-Api-Key error, open /api/agents
echo and check whether it is only a stale last_error field.
echo.

start http://localhost:8766
pause
