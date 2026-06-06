# ============================================================
# build_electron.ps1 - Task Hounds Electron Build Script
# Usage:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   .\build_electron.ps1
# ============================================================

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "      Task Hounds Electron Builder         " -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# -- 1. Check Node.js --------------------------------------------------
Write-Host "[*] Checking Node.js..." -ForegroundColor Yellow
try {
    $nodeVersion = node --version 2>&1
    Write-Host "  [OK] Node.js $nodeVersion" -ForegroundColor Green
} catch {
    Write-Host "  [ERROR] Node.js not found. Please install Node.js 18+: https://nodejs.org" -ForegroundColor Red
    exit 1
}

# -- 2. Check npm ------------------------------------------------------
Write-Host "[*] Checking npm..." -ForegroundColor Yellow
try {
    $npmVersion = npm --version 2>&1
    Write-Host "  [OK] npm $npmVersion" -ForegroundColor Green
} catch {
    Write-Host "  [ERROR] npm not found." -ForegroundColor Red
    exit 1
}

# -- 3. Check Python ---------------------------------------------------
Write-Host "[*] Checking Python..." -ForegroundColor Yellow
try {
    $pyVersion = python --version 2>&1
    Write-Host "  [OK] $pyVersion" -ForegroundColor Green
} catch {
    Write-Host "  [WARN] Python command not found. The packaged .exe requires Python on the target machine." -ForegroundColor Yellow
}

# -- 4. Change to electron directory -----------------------------------
$scriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$electronDir = Join-Path $scriptDir "apps\electron"

if (-not (Test-Path $electronDir)) {
    Write-Host "  [ERROR] Directory not found: $electronDir" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "[*] Changing to: $electronDir" -ForegroundColor Yellow
Set-Location $electronDir

# -- 5. Install npm dependencies ---------------------------------------
Write-Host ""
Write-Host "[*] Installing npm dependencies (electron + electron-builder)..." -ForegroundColor Yellow
npm install
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [ERROR] npm install failed." -ForegroundColor Red
    exit 1
}
Write-Host "  [OK] Dependencies installed." -ForegroundColor Green

# -- 6. Run electron-builder -------------------------------------------
Write-Host ""
Write-Host "[*] Building package (this may take a few minutes)..." -ForegroundColor Yellow
npm run dist
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [ERROR] Build failed. See output above for details." -ForegroundColor Red
    exit 1
}

# -- 7. Show output results --------------------------------------------
$distDir = Join-Path $electronDir "dist"
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  [OK] Build succeeded!                    " -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "Output directory:" -ForegroundColor Cyan
Write-Host "   $distDir" -ForegroundColor White
Write-Host ""

if (Test-Path $distDir) {
    $exeFiles = Get-ChildItem -Path $distDir -Filter "*.exe" -Recurse
    if ($exeFiles.Count -gt 0) {
        Write-Host "Found the following .exe files:" -ForegroundColor Cyan
        foreach ($f in $exeFiles) {
            $sizeMB = [math]::Round($f.Length / 1MB, 1)
            Write-Host "  [OK] $($f.FullName)  ($sizeMB MB)" -ForegroundColor Green
        }
    } else {
        Write-Host "  [WARN] No .exe files found in dist directory. Please check: $distDir" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Installer notes:" -ForegroundColor Cyan
Write-Host "  - Run *-Setup.exe to install Task Hounds." -ForegroundColor White
Write-Host "  - Shortcuts will be created on the Desktop and Start Menu." -ForegroundColor White
Write-Host "  - Target machine requires Python 3.9+ and relevant packages." -ForegroundColor White
Write-Host ""
