<#
.SYNOPSIS
    Build the Task Hounds Electron portable app.

.DESCRIPTION
    Builds the React frontend, freezes the Supervisor/API/GraphFlow worker into
    a self-contained Python runtime, then packages everything with Electron.

.NOTES
    Requires Node.js, Python 3.11+, project Python dependencies, and PyInstaller.
#>

param(
    [switch]$SkipFrontend,
    [switch]$SkipElectron,
    [switch]$StopRunningApp
)

$ErrorActionPreference = 'Stop'

$PROJECT_ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$UI_WEB = Join-Path $PROJECT_ROOT 'ui\web'
$UI_DESKTOP = Join-Path $PROJECT_ROOT 'ui\desktop'
$OUTPUT_DIR = Join-Path $UI_DESKTOP 'dist'
$RUNTIME_BUILD = Join-Path $PROJECT_ROOT 'build\pyinstaller'
$RUNTIME_DIST = Join-Path $PROJECT_ROOT 'dist\task-hounds-runtime'
$EXTRA_BIN = Join-Path $UI_DESKTOP 'extra-bin'
$EXTRA_RUNTIME = Join-Path $EXTRA_BIN 'task-hounds-runtime'
$SPEC_FILE = Join-Path $PROJECT_ROOT 'pyinstaller-server.spec'
$BUILD_VENV = Join-Path $PROJECT_ROOT '.build-venv'
$BUILD_PYTHON = Join-Path $BUILD_VENV 'Scripts\python.exe'
$REQUIREMENTS = Join-Path $PROJECT_ROOT 'requirements.txt'

function Test-Command {
    param([string]$Cmd)
    try {
        Get-Command $Cmd -ErrorAction SilentlyContinue | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Write-Step {
    param([string]$Msg)
    Write-Host "`n=== $Msg ===" -ForegroundColor Cyan
}

Write-Host "Task Hounds Build Script" -ForegroundColor Green
Write-Host "Project root: $PROJECT_ROOT"
Write-Host "Mode: self-contained Electron + Python runtime`n"
Write-Host "Backend default: FastAPI on 127.0.0.1:8766`n"

Write-Step "Checking prerequisites"
if (-not (Test-Command 'node')) {
    Write-Error "Node.js is not installed or not in PATH. Please install Node.js 18+."
    exit 1
}
if (-not (Test-Command 'npm')) {
    Write-Error "npm is not installed or not in PATH."
    exit 1
}
Write-Host "  Node.js: $(node --version)"
Write-Host "  npm    : $(npm --version)"
if (-not (Test-Command 'py')) {
    Write-Error "Python 3.11+ is required to build the desktop runtime."
    exit 1
}
py -3.12 --version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Python 3.12 is required for reproducible release builds."
    exit 1
}
Write-Host "  Python : $(py -3.12 --version)"

if ($StopRunningApp) {
    Write-Step "Stopping running Task Hounds processes"
    Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ProcessName -like 'Task Hounds*' } |
        ForEach-Object {
            Write-Host "  Stopping $($_.ProcessName) pid=$($_.Id)"
            Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
        }
}

if (-not $SkipFrontend) {
    Write-Step "Building frontend"
    Push-Location $UI_WEB
    try {
        if (-not (Test-Path 'node_modules')) {
            Write-Host "  Running npm ci..."
            npm ci
        }
        Write-Host "  Running npm run build..."
        npm run build
        if ($LASTEXITCODE -ne 0) {
            throw "Frontend build failed"
        }
    } finally {
        Pop-Location
    }
} else {
    Write-Step "Skipping frontend build"
}

Write-Step "Checking desktop dependencies"
Push-Location $UI_DESKTOP
try {
    if (-not (Test-Path 'node_modules')) {
        Write-Host "  Running npm ci..."
        npm ci
        if ($LASTEXITCODE -ne 0) {
            throw "Desktop npm ci failed"
        }
    }
} finally {
    Pop-Location
}

Write-Step "Building self-contained runtime"
if (-not (Test-Path $BUILD_PYTHON)) {
    Write-Host "  Creating Python 3.12 build environment..."
    py -3.12 -m venv $BUILD_VENV
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create the Python 3.12 build environment"
    }
}

Write-Host "  Installing locked runtime build dependencies..."
& $BUILD_PYTHON -m pip install --disable-pip-version-check -r $REQUIREMENTS pyinstaller
if ($LASTEXITCODE -ne 0) {
    throw "Runtime build dependency installation failed"
}
& $BUILD_PYTHON -m pip install --disable-pip-version-check --no-deps -e $PROJECT_ROOT
if ($LASTEXITCODE -ne 0) {
    throw "Task Hounds package installation failed"
}

& $BUILD_PYTHON -m PyInstaller $SPEC_FILE --clean --noconfirm `
    --workpath $RUNTIME_BUILD `
    --distpath (Join-Path $PROJECT_ROOT 'dist')
if ($LASTEXITCODE -ne 0) {
    throw "Desktop runtime build failed"
}
if (-not (Test-Path (Join-Path $RUNTIME_DIST 'task-hounds-runtime.exe'))) {
    throw "Runtime executable was not produced: $RUNTIME_DIST"
}

if (Test-Path $EXTRA_RUNTIME) {
    Remove-Item -LiteralPath $EXTRA_RUNTIME -Recurse -Force
}
New-Item -ItemType Directory -Path $EXTRA_BIN -Force | Out-Null
Copy-Item -Path $RUNTIME_DIST -Destination $EXTRA_RUNTIME -Recurse -Force
Write-Host "  Runtime: $EXTRA_RUNTIME"

if (-not $SkipElectron) {
    Write-Step "Building Electron portable exe"
    Push-Location $UI_DESKTOP
    try {
        $env:ELECTRON_CACHE = Join-Path $UI_DESKTOP '.electron-cache'
        $env:ELECTRON_BUILDER_CACHE = Join-Path $UI_DESKTOP '.electron-builder-cache'

        Write-Host "  ELECTRON_CACHE        = $env:ELECTRON_CACHE"
        Write-Host "  ELECTRON_BUILDER_CACHE= $env:ELECTRON_BUILDER_CACHE"
        Write-Host "  Running npx electron-builder --win --publish never..."

        npx electron-builder --win --publish never
        if ($LASTEXITCODE -ne 0) {
            throw "electron-builder failed"
        }
    } finally {
        Pop-Location
    }
} else {
    Write-Step "Skipping Electron build"
}

Write-Step "Build complete"
if (Test-Path $OUTPUT_DIR) {
    Get-ChildItem $OUTPUT_DIR -Filter '*.exe' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        ForEach-Object {
            Write-Host "  EXE: $($_.FullName)" -ForegroundColor Green
        }
} else {
    Write-Host "  No dist directory found yet: $OUTPUT_DIR" -ForegroundColor Yellow
}
