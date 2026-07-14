# Changelog

## 0.1.0 ALPHA — 2026-07-14

- Added a loopback FastAPI service with health, model status, chat, streaming chat, and
  conversation endpoints.
- Added streaming to the agent loop while preserving tools, persistence, personality adaptation,
  and response auditing.
- Added configurable `MASTER_NUM_CTX`, defaulting to 32768, to every Ollama chat request.
- Connected the existing Tauri Milestone 1 interface to the Python service instead of Ollama.
- Added API, persistence, and Ollama context-length tests.
- Added the branded Tauri desktop integration and public alpha release preparation.

## 0.1.0 — 2026-07-14

### Added

- Initial Project Master constitution and architecture.
- Ollama chat provider with native tool-call loop.
- SQLite memory, conversation history, claim ledger, and evidence storage.
- Adaptive style profile that mirrors communication style rather than beliefs.
- Built-in calculator, time, workspace, memory, and evidence tools.
- CLI commands for chat, one-shot questions, diagnostics, claims, memories, and audits.
- Windows bootstrap scripts, schemas, tests, examples, and GitHub issue templates.
