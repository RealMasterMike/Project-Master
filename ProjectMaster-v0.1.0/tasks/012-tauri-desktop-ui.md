# Task: Tauri Desktop UI

**Status:** In progress — API and Milestone 1 chat integration complete

## Objective

Build a desktop interface for chat, evidence, memory, tasks, tools, permissions, and voice.

## Next steps

- [x] Define core service API.
- [x] Connect the existing Tauri shell to the Python engine.
- [x] Build streaming chat; tool events are exposed by the API but not yet visualized.
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

## Next task

Package and manage the Python service lifecycle from Tauri, then add visible tool activity and
conversation selection without moving reasoning behavior into the frontend.

## Governing principle

Changes must improve reliability or clearly enable a later reliability improvement.
