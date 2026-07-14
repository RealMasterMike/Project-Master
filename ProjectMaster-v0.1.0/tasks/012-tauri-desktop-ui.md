# Task: Tauri Desktop UI

**Status:** In progress — packaged chat integration complete

## Objective

Build a desktop interface for chat, evidence, memory, tasks, tools, permissions, and voice.

## Next steps

- [x] Define core service API.
- [x] Connect the existing Tauri shell to the Python engine.
- [x] Build streaming chat; tool events are exposed by the API but not yet visualized.
- [x] Package and manage the Python API lifecycle from Tauri.
- [x] Recover from a crashed backend through the conversation Retry action.
- [ ] Build evidence ledger view.
- [ ] Build settings and permissions.

## Completion evidence

- UI remains separate from core reasoning.
- Every tool action is inspectable.
- Memory and permissions are user-controlled.

## Current completion evidence

- The API binds to `127.0.0.1` and reuses the CLI runtime.
- Health, model status, chat, streaming chat, and conversation endpoints have tests.
- The React client no longer calls Ollama directly.
- Backend tests, changed-file lint, frontend build, Tauri check, and a live Ollama stream pass.
- The Windows installer includes the Python engine; installed users do not start `master serve`.
- Forced backend termination produces an inline error, and one Retry restarts it and completes chat.

## Next task

Add visible tool activity and conversation selection without moving reasoning behavior into the
frontend.

## Governing principle

Changes must improve reliability or clearly enable a later reliability improvement.
