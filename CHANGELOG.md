# Changelog

All notable Project Master desktop releases are recorded here.

## 0.2.0 ALPHA — 2026-07-14

### Added

- Added the Conversation Library: create a clean session, reopen saved conversations, and review
  their locally stored message history from a persistent workspace sidebar.
- Added a grounded first-session intake and a literal capability contract for Project Master.

### Changed

- Replaced generic, overly theatrical assistant behavior with a calmer default voice. Unprompted
  emojis, hype, glitter, magic, space, aliens, and whimsical roleplay are now discouraged.

### Verification

- Frontend conversation protocol tests, prompt-contract tests, memory-authorization tests, and the
  frontend production build passed before packaging.

## Release gate

No build may be uploaded or published unless all of the following are complete:

- Add a versioned entry to this changelog.
- Draft version-matched GitHub Release notes describing changes, fixes, known issues, and
  verification.
- Build and test the exact artifacts intended for upload.
- Generate checksums after the final artifacts are produced.
- Confirm the changelog, release notes, artifact filenames, application version, tag, and checksums
  all use the same version.

If any item is missing, the release is blocked. Finish the release documentation before uploading
another build.

`CHANGELOG.md` is the only rolling changelog kept on the default branch. Do not add separate
per-version changelog or release-note files to the default branch. Version-specific notes belong in
the matching GitHub Release; historical tags preserve the repository exactly as it existed for that
release.

## 0.1.3 ALPHA — 2026-07-14

### Added

- Added a versioned, declarative layout schema with validated operations for panel visibility,
  collapsed state, ordering, tab grouping, and constrained widths.
- Added an interface customization panel with chat and panel sizing, collapse/expand behavior,
  named saved layouts, local persistence, Undo, and Reset to default.
- Added executable break-tests for invalid panels, arbitrary operations, forbidden states, stale
  revisions, invalid dimensions, oversized batches, transactional rollback, and corrupt storage.

### Fixed

- Stop now sends an explicit cancellation request to the Python engine, which closes the active
  Ollama response and releases the provider before another prompt begins.
- Durable memory can no longer be created from ordinary or exploratory chat by a model tool call;
  a user must explicitly ask Project Master to remember, save, or store the information.
- The desktop app now rejects a mismatched backend already using its local port instead of silently
  attaching a newer interface to an older backend and failing chat requests.

### Repository maintenance

- Removed obsolete build handoffs, portable AI prompts, duplicate release-note files, and stale task
  tracking documents from the default branch.
- Consolidated release history into this rolling changelog.

### Known issues

- Ollama and at least one compatible local model are still required.
- Windows installers are not code-signed and may trigger a Microsoft reputation warning.
- Tool, memory, evidence, settings, and conversation-management interfaces are not built yet.

### Verification

- Targeted memory-authorization tests and layout/client protocol tests passed before the release build.
- Frontend production build, packaged backend build, and Windows NSIS/MSI installer builds passed.

## 0.1.1 ALPHA — 2026-07-14

### Changed

- Packaged the existing Python AI engine with the Tauri desktop installers.
- Added desktop-managed backend startup, readiness checks, application-data paths, logging, and
  shutdown.
- Routed connection and conversation Retry actions through the managed backend lifecycle.

### Fixed

- Fixed the v0.1.0 installer opening with the backend permanently offline.
- Added stale-backend replacement and recovery after a backend crash.
- Ensured the complete backend process tree stops when Project Master exits.

### Known issues

- Ollama and at least one compatible local model are still required.
- Windows installers are not code-signed and may trigger a Microsoft reputation warning.
- Tool, memory, evidence, settings, and conversation-management interfaces are not built yet.

### Verification

- Python tests and lint passed.
- Frontend production build passed.
- Rust lifecycle tests, formatting, and lint passed.
- NSIS and MSI installer builds passed.
- Installed backend startup, shutdown, crash detection, and one-click recovery passed.

## 0.1.0 ALPHA — 2026-07-14

### Added

- Released the first public Project Master desktop alpha.
- Connected the React and Tauri interface to the Python AI engine.
- Added streaming chat, model selection, cancellation, conversation persistence, memory, tools,
  evidence tracking, adaptive communication style, and response auditing.

### Known issues

- The Windows installer did not bundle or start the Python backend, so a normal installation opened
  in an offline state. This was corrected in 0.1.1 ALPHA.
