# Project Master — ALPHA v0.1.0

Project Master is a local-first research, reasoning, and creation assistant centered on epistemic
reliability: separating facts, claims, evidence, inference, and uncertainty.

## Included

- Branded Tauri 2 + React desktop chat interface
- Local Python AI engine with Ollama support
- Streaming responses and cancellation
- Configurable model and 32768-token default context
- SQLite conversations and memory
- Tool calling with workspace-scoped file access
- Claims and evidence ledger
- Adaptive communication style and response auditing
- Windows MSI and NSIS installer artifacts for the desktop interface

## Alpha limitations

- The Python backend must be installed and started separately with `master serve`.
- Ollama and at least one compatible local model are required.
- The 32768-token context is configurable. Start at 8192 on 8 GB GPUs and raise
  it only after verifying stable model performance.
- The Windows installers are not code-signed and may trigger reputation warnings.
- Tool activity, memory, evidence, and conversation-selection interfaces are not implemented yet.
- Expect breaking changes and database migrations in later alpha versions.

## Run

Backend terminal:

```powershell
cd ProjectMaster-v0.1.0
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
.\.venv\Scripts\Activate.ps1
master serve
```

Desktop development terminal:

```powershell
npm install
npm run tauri dev
```

## Creator

Created by [Master Mike](https://linktr.ee/realmastermike). Follow the build on
[YouTube](https://www.youtube.com/@RealMasterMike?sub_confirmation=1) or support development through
[Streamlabs](https://streamlabs.com/mastermike/tip).
