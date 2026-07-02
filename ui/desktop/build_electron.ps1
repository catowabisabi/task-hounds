<#
.SYNOPSIS
    Compatibility entry point for the Task Hounds desktop build.

.DESCRIPTION
    The canonical build pipeline lives at the repository root. Keeping this
    wrapper prevents older instructions from accidentally producing an
    Electron-only build without the Supervisor, API, and GraphFlow worker.
#>

param(
    [switch]$SkipFrontend,
    [switch]$SkipElectron,
    [switch]$StopRunningApp
)

$rootScript = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'build_exe.ps1'
if (-not (Test-Path $rootScript)) {
    throw "Task Hounds build script not found: $rootScript"
}

& $rootScript `
    -SkipFrontend:$SkipFrontend `
    -SkipElectron:$SkipElectron `
    -StopRunningApp:$StopRunningApp
exit $LASTEXITCODE
