# Tauri Desktop UI

The planned desktop application should remain a client of the Project Master core rather than embedding all logic in the interface.

Initial screens:

- chat and tool activity;
- claims and evidence ledger;
- memory inspector and deletion controls;
- task board;
- model and provider settings;
- permissions;
- voice controls;
- audit panel.

The Python engine is exposed through a loopback HTTP service packaged as a PyInstaller sidecar.
Tauri starts it on `127.0.0.1:8765`, waits for readiness, and stops the full process tree when the
desktop app exits. Desktop data lives under Tauri's per-user application-data directory. The CLI
and standalone `master serve` entry point remain available for development and automation.

Both the connection banner and conversation-level Retry paths must invoke Tauri's managed-backend
command before retrying API work. A sidecar older than the startup grace period whose endpoint is
closed is treated as stale and replaced. This prevents Windows from leaving the interface stuck
behind a dead child-process handle.
