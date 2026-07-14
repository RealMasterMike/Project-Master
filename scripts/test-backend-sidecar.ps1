[CmdletBinding()]
param(
    [string]$BinaryPath = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$targetTriple = (& rustc --print host-tuple).Trim()
$extension = if ($targetTriple -like "*-windows-*") { ".exe" } else { "" }
if (-not $BinaryPath) {
    $BinaryPath = Join-Path $repoRoot "src-tauri\binaries\project-master-backend-$targetTriple$extension"
}
if (-not (Test-Path $BinaryPath)) {
    throw "Backend sidecar not found at $BinaryPath. Run build-backend-sidecar.ps1 first."
}

$probe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$probe.Start()
$port = ([System.Net.IPEndPoint]$probe.LocalEndpoint).Port
$probe.Stop()

$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$testRoot = Join-Path $tempRoot "project-master-sidecar-$([guid]::NewGuid())"
$resolvedTestRoot = [System.IO.Path]::GetFullPath($testRoot)
if (-not $resolvedTestRoot.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -or
    -not (Split-Path -Leaf $resolvedTestRoot).StartsWith("project-master-sidecar-")) {
    throw "Refusing to use an unexpected sidecar test directory: $resolvedTestRoot"
}
New-Item -ItemType Directory -Force $testRoot | Out-Null

$environmentNames = @(
    "MASTER_API_PORT",
    "MASTER_CONFIG",
    "MASTER_DB_PATH",
    "MASTER_WORKSPACE_ROOT",
    "MASTER_LOG_PATH"
)
$previousEnvironment = @{}
foreach ($name in $environmentNames) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}

$process = $null
try {
    $env:MASTER_API_PORT = "$port"
    $env:MASTER_CONFIG = Join-Path $testRoot "config.yaml"
    $env:MASTER_DB_PATH = Join-Path $testRoot "master.db"
    $env:MASTER_WORKSPACE_ROOT = Join-Path $testRoot "workspace"
    $env:MASTER_LOG_PATH = Join-Path $testRoot "backend.log"

    $startArgs = @{
        FilePath = $BinaryPath
        PassThru = $true
    }
    if ($IsWindows -or $env:OS -eq "Windows_NT") {
        $startArgs.WindowStyle = "Hidden"
    }
    $process = Start-Process @startArgs

    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    $status = $null
    do {
        if ($process.HasExited) {
            throw "Backend sidecar exited early with code $($process.ExitCode)."
        }
        try {
            $status = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/v1/models/status" -TimeoutSec 2
        } catch {
            Start-Sleep -Milliseconds 250
        }
    } while (-not $status -and [DateTime]::UtcNow -lt $deadline)

    if (-not $status) {
        $logPath = Join-Path $testRoot "backend.log"
        $logText = if (Test-Path $logPath) { Get-Content -Raw $logPath } else { "No log created." }
        throw "Backend sidecar did not become ready within 30 seconds. $logText"
    }
    if ($status.num_ctx -ne 32768) {
        throw "Expected default context length 32768, received $($status.num_ctx)."
    }
    if (-not (Test-Path (Join-Path $testRoot "master.db"))) {
        throw "Backend sidecar did not create its database in the configured data directory."
    }

    Write-Output "Backend sidecar smoke test passed on 127.0.0.1:$port."
} finally {
    $cleanupError = $null
    if ($process -and -not $process.HasExited) {
        if ($IsWindows -or $env:OS -eq "Windows_NT") {
            & taskkill /PID $process.Id /T /F | Out-Null
        } else {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
        $process.WaitForExit(5000) | Out-Null
    }
    $remainingSidecars = Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -and [System.IO.Path]::GetFullPath($_.Path) -eq [System.IO.Path]::GetFullPath($BinaryPath)
    }
    if ($remainingSidecars) {
        $cleanupError = "Backend sidecar smoke test left a child process running."
    }
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], "Process")
    }
    Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force -ErrorAction SilentlyContinue
    if ($cleanupError) {
        throw $cleanupError
    }
}
