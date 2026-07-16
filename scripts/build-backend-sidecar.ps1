[CmdletBinding()]
param(
    [string]$PythonCommand = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $repoRoot "ProjectMaster-v0.1.0"
$venvRoot = Join-Path $backendRoot ".venv-packaging"
$venvPython = if ($IsWindows -or $env:OS -eq "Windows_NT") {
    Join-Path $venvRoot "Scripts\python.exe"
} else {
    Join-Path $venvRoot "bin/python"
}

if (-not (Test-Path $venvPython)) {
    if ($PythonCommand) {
        & $PythonCommand -m venv $venvRoot
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.12 -m venv $venvRoot
    } elseif (Get-Command python3 -ErrorAction SilentlyContinue) {
        & python3 -m venv $venvRoot
    } else {
        throw "Python 3.11 or newer is required to build the Project Master backend sidecar."
    }
}

& $venvPython -m pip install --disable-pip-version-check --quiet -e "$backendRoot[packaging]"
if ($LASTEXITCODE -ne 0) {
    throw "Installing backend packaging dependencies failed."
}

$desktopVersion = (Get-Content (Join-Path $repoRoot "package.json") -Raw | ConvertFrom-Json).version
$backendPackageVersion = (& $venvPython -c (
    "from importlib.metadata import version; print(version('project-master-ai'))"
)).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Unable to read the installed backend package version."
}
$backendRuntimeVersion = (& $venvPython -c (
    "import project_master; print(project_master.__version__)"
)).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Unable to read the backend runtime version."
}
if ($backendPackageVersion -ne $desktopVersion -or $backendRuntimeVersion -ne $desktopVersion) {
    throw "Release version mismatch: desktop $desktopVersion, backend package $backendPackageVersion, backend runtime $backendRuntimeVersion."
}

$targetTriple = (& rustc --print host-tuple).Trim()
if (-not $targetTriple) {
    throw "Unable to determine the Rust target triple."
}

$isWindowsTarget = $targetTriple -like "*-windows-*"
$extension = if ($isWindowsTarget) { ".exe" } else { "" }
$buildRoot = Join-Path $backendRoot "build\sidecar"
$distRoot = Join-Path $buildRoot "dist"
$workRoot = Join-Path $buildRoot "work"
$specRoot = Join-Path $buildRoot "spec"
$binaryRoot = Join-Path $repoRoot "src-tauri\binaries"
$entryPoint = Join-Path $backendRoot "src\project_master\sidecar.py"

New-Item -ItemType Directory -Force $distRoot, $workRoot, $specRoot, $binaryRoot | Out-Null

$pyInstallerArgs = @(
    "--noconfirm",
    "--clean",
    "--onefile",
    "--name", "project-master-backend",
    "--collect-data", "project_master",
    "--distpath", $distRoot,
    "--workpath", $workRoot,
    "--specpath", $specRoot,
    "--paths", (Join-Path $backendRoot "src")
)
if ($isWindowsTarget) {
    $pyInstallerArgs += "--noconsole"
}
$pyInstallerArgs += $entryPoint

& $venvPython -m PyInstaller @pyInstallerArgs
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed to build the Project Master backend sidecar."
}

$builtBinary = Join-Path $distRoot "project-master-backend$extension"
$tauriBinary = Join-Path $binaryRoot "project-master-backend-$targetTriple$extension"
if (-not (Test-Path $builtBinary)) {
    throw "PyInstaller completed without producing $builtBinary."
}

$copyAttempts = 40
for ($attempt = 1; $attempt -le $copyAttempts; $attempt++) {
    try {
        Copy-Item -Force $builtBinary $tauriBinary
        break
    } catch [System.IO.IOException] {
        if ($attempt -eq $copyAttempts) {
            throw
        }
        Start-Sleep -Milliseconds 250
    }
}
Write-Output $tauriBinary
