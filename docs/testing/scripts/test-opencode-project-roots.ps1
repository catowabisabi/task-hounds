param(
  [string[]]$ProjectDirs = @(
    "C:\Users\enoma\Desktop\projects\cato-todo",
    "C:\Users\enoma\Desktop\projects\task-hounds-projects",
    "C:\Users\enoma\Desktop\projects\test"
  ),
  [int[]]$Ports = @(40961, 40962, 40963),
  [switch]$KeepAlive
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

function Find-OpenCode {
  $name = if ($IsWindows) { "opencode.exe" } else { "opencode" }
  $managed = Join-Path $RepoRoot "core\runtime\opencode_runtime\node_modules\opencode-ai\bin\$name"
  if (Test-Path -LiteralPath $managed) { return $managed }

  throw "managed OpenCode executable not found; run installation.cmd"
}

function Wait-OpenCode {
  param([int]$Port)

  $url = "http://127.0.0.1:$Port/session"
  $deadline = (Get-Date).AddSeconds(30)
  while ((Get-Date) -lt $deadline) {
    try {
      Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2 | Out-Null
      return
    } catch {
      Start-Sleep -Milliseconds 500
    }
  }
  throw "OpenCode on port $Port did not become ready"
}

function Ask-Root {
  param(
    [string]$OpenCode,
    [string]$ProjectDir,
    [int]$Port
  )

  $prompt = @"
Reply with ONLY this information, no explanation:
1. current working directory
2. project root directory
3. whether you can see this exact marker path: $ProjectDir
"@

  $env:XDG_CONFIG_HOME = Join-Path $RepoRoot "core\runtime\opencode_home\.config"
  $env:XDG_DATA_HOME = Join-Path $RepoRoot "core\runtime\opencode_home\.local\share"
  $env:OPENCODE_CONFIG_DIR = Join-Path $RepoRoot "core\runtime\opencode_config"

  $output = & $OpenCode run `
    --attach "http://127.0.0.1:$Port" `
    --format json `
    --thinking `
    --dangerously-skip-permissions `
    $prompt 2>&1

  $texts = @()
  foreach ($line in $output) {
    $s = [string]$line
    if (-not $s.Trim().StartsWith("{")) { continue }
    try {
      $event = $s | ConvertFrom-Json
      if ($event.type -eq "text") {
        if ($event.part.text) { $texts += [string]$event.part.text }
        elseif ($event.text) { $texts += [string]$event.text }
      }
    } catch {
    }
  }

  return ($texts -join "`n").Trim()
}

function Start-OpenCodeServe {
  param(
    [string]$OpenCode,
    [string]$Dir,
    [int]$Port,
    [string]$Stdout,
    [string]$Stderr
  )

  $psi = [System.Diagnostics.ProcessStartInfo]::new()
  $psi.FileName = $OpenCode
  $psi.WorkingDirectory = $Dir
  $psi.UseShellExecute = $false
  $psi.CreateNoWindow = $true
  $psi.RedirectStandardOutput = $false
  $psi.RedirectStandardError = $false
  $psi.Arguments = "serve --hostname 127.0.0.1 --port $Port"

  $proc = [System.Diagnostics.Process]::new()
  $proc.StartInfo = $psi
  [void]$proc.Start()
  return [pscustomobject]@{ Process = $proc; OutStream = $null; ErrStream = $null }
}

if ($ProjectDirs.Count -ne $Ports.Count) {
  throw "ProjectDirs count must equal Ports count"
}

$opencode = Find-OpenCode
$started = @()

Write-Host "OpenCode: $opencode" -ForegroundColor Cyan

try {
  for ($i = 0; $i -lt $ProjectDirs.Count; $i++) {
    $dir = $ProjectDirs[$i]
    $port = $Ports[$i]

    if (-not (Test-Path -LiteralPath $dir -PathType Container)) {
      throw "Project folder not found: $dir"
    }

    Write-Host "`n=== Starting serve for $dir on port $port ===" -ForegroundColor Yellow

    $logOut = Join-Path $env:TEMP "opencode-root-test-$port.out.log"
    $logErr = Join-Path $env:TEMP "opencode-root-test-$port.err.log"

    $startedProcess = Start-OpenCodeServe -OpenCode $opencode -Dir $dir -Port $port -Stdout $logOut -Stderr $logErr
    $proc = $startedProcess.Process

    $started += [pscustomobject]@{
      Port = $port
      Process = $proc
      Dir = $dir
      Stdout = $logOut
      Stderr = $logErr
      OutStream = $startedProcess.OutStream
      ErrStream = $startedProcess.ErrStream
    }
    Wait-OpenCode -Port $port
    Write-Host "Ready: http://127.0.0.1:$port pid=$($proc.Id)" -ForegroundColor Green
  }

  Write-Host "`n=== Asking each server for its root ===" -ForegroundColor Cyan
  foreach ($item in $started) {
    Write-Host "`n--- Port $($item.Port) / $($item.Dir) ---" -ForegroundColor Cyan
    $answer = Ask-Root -OpenCode $opencode -ProjectDir $item.Dir -Port $item.Port
    if ($answer) {
      Write-Host $answer
    } else {
      Write-Host "(no text answer returned)" -ForegroundColor Red
    }
  }

  if ($KeepAlive) {
    Write-Host "`nKeeping servers alive:" -ForegroundColor Yellow
    foreach ($item in $started) {
      Write-Host "port=$($item.Port) pid=$($item.Process.Id) dir=$($item.Dir)"
    }
  }
} finally {
  if (-not $KeepAlive) {
    Write-Host "`nStopping test servers..." -ForegroundColor Yellow
    foreach ($item in $started) {
      try {
        if (-not $item.Process.HasExited) {
          Stop-Process -Id $item.Process.Id -Force -ErrorAction SilentlyContinue
        }
        if ($item.OutStream) { $item.OutStream.Dispose() }
        if ($item.ErrStream) { $item.ErrStream.Dispose() }
      } catch {
      }
    }
  }
}
