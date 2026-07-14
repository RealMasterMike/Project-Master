[CmdletBinding()]
param(
    [string]$AppPath = "$env:LOCALAPPDATA\Project Master\master.exe"
)

$ErrorActionPreference = "Stop"

$resolvedApp = [System.IO.Path]::GetFullPath($AppPath)
if (-not (Test-Path $resolvedApp)) {
    throw "Installed Project Master executable not found at $resolvedApp."
}

$existingListener = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if ($existingListener) {
    throw "Port 8765 is already in use. Close any Project Master backend before this test."
}

$appProcess = $null
$testError = $null
try {
    $appProcess = Start-Process -FilePath $resolvedApp -PassThru -WindowStyle Normal
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    $health = $null
    do {
        if ($appProcess.HasExited) {
            throw "Installed Project Master exited early with code $($appProcess.ExitCode)."
        }
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/v1/health" -TimeoutSec 2
        } catch {
            Start-Sleep -Milliseconds 250
        }
    } while (-not $health -and [DateTime]::UtcNow -lt $deadline)

    if (-not $health) {
        throw "Installed Project Master did not start its backend within 30 seconds."
    }
    if ($health.version -ne "0.1.1") {
        throw "Expected backend version 0.1.1, received $($health.version)."
    }

    $installedBackend = [System.IO.Path]::GetFullPath(
        (Join-Path (Split-Path -Parent $resolvedApp) "project-master-backend.exe")
    )
    $backendProcesses = Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -and [System.IO.Path]::GetFullPath($_.Path) -eq $installedBackend
    }
    if (-not $backendProcesses) {
        throw "The API responded, but no installed Project Master backend process was found."
    }

    if (-not $appProcess.CloseMainWindow()) {
        throw "Project Master did not accept a normal window-close request."
    }
    if (-not $appProcess.WaitForExit(10000)) {
        throw "Project Master did not exit within 10 seconds after its window closed."
    }

    $shutdownDeadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        $listener = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
        $remainingBackend = Get-Process -ErrorAction SilentlyContinue | Where-Object {
            $_.Path -and [System.IO.Path]::GetFullPath($_.Path) -eq $installedBackend
        }
        if (-not $listener -and -not $remainingBackend) {
            break
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $shutdownDeadline)

    if ($listener -or $remainingBackend) {
        throw "Project Master closed, but its packaged backend remained running."
    }

    Write-Output "Installed app test passed: backend auto-started at v0.1.1 and stopped cleanly."
} catch {
    $testError = $_
} finally {
    if ($appProcess -and -not $appProcess.HasExited) {
        & taskkill /PID $appProcess.Id /T /F | Out-Null
        $appProcess.WaitForExit(5000) | Out-Null
    }
}

if ($testError) {
    throw $testError
}
