$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

if (-not (Test-Path ".venv")) {
    py -m venv .venv
}

& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -e ".[dev]"

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
}

Write-Host ""
Write-Host "Project Master v0.1 installed." -ForegroundColor Green
Write-Host "Edit .env if your Ollama model has a different name."
Write-Host "Run: .\.venv\Scripts\Activate.ps1"
Write-Host "Then: master doctor"
Write-Host "Then: master chat"
