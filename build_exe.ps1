<#
.SYNOPSIS
    Build the Task Hounds Electron portable app.

.DESCRIPTION
    Current desktop builds do not package a separate Python server exe.
    This script builds the React frontend, then runs electron-builder so the
    Task Hounds exe contains the current frontend and bundled source resources.

.NOTES
    Requires Node.js and ui/desktop dependencies.
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
Write-Host "Mode: Electron app only; no PyInstaller/server exe`n"
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
