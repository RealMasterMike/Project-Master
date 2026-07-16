# Changelog

All notable Project Master desktop releases are recorded here.

## 0.2.2 ALPHA — 2026-07-15

### Added

- Added signed in-app desktop updates with an explicit user confirmation before download, install,
  and restart.
- Added a daily update-check policy for alpha builds and a weekly policy for beta and stable builds.
- Added an automated prerelease workflow that signs Windows updater artifacts and maintains a
  rolling GitHub alpha update channel.

### Changed

- Existing v0.2.1 users must manually install v0.2.2 once to receive the updater. Beginning with
  this release, future updates can be discovered and installed from within Project Master.

### Security

- Updater packages are signed with a dedicated password-protected Tauri key and verified against
  the public key embedded in the desktop application before installation.

### Known issues

- Ollama and at least one compatible local model are still required.
- The Windows installer is updater-signed but not Authenticode-signed, so Microsoft reputation
  warnings can still appear during the initial manual installation.
- Users on v0.2.1 or earlier cannot receive this release automatically and must install it manually
  from GitHub once.

### Verification

- Backend Ruff checks passed and all 41 backend tests passed.
- All 17 frontend tests, the production frontend build, and the high-severity npm audit passed with
  zero reported vulnerabilities.
- Rust formatting, strict Clippy checks, and all five native lifecycle/version tests passed.
- The packaged v0.2.2 backend sidecar passed its health smoke test.
- The exact NSIS and MSI installers built successfully with matching Tauri updater signatures and
  SHA-256 checksums.
- The NSIS installer upgraded the local installation in place; the installed v0.2.2 application
  auto-started and cleanly stopped its packaged backend.
- A live installed-app test through Ollama and `qwen3:8b` returned exactly `OLLAMA_V022_OK`.

## 0.2.1 ALPHA — 2026-07-15

### Changed

- Added a conservative communication-fidelity foundation to the Python engine. It now preserves
  literal user text alongside labeled intent, ambiguity, prior context, and response-planning data
  before a model responds.
- Replaced the profile's absence-based style assumptions with an auditable communication profile for
  explicit preferences, corrections, disliked response patterns, source, confidence, examples,
  scope, timestamps, and superseded records.
- Added local communication-profile inspection and explicit feedback endpoints so a future interface
  can show the profile and record a deliberate correction without converting it into a factual memory.
- Added a local **Communication** tab to the desktop Customize panel. It shows active interaction
  rules and records scoped corrections such as changed meaning, assumptions, unwanted advice,
  repetition, or ignored context.
- Added context-aware response checks for unsupported user attributions, reintroduced corrections,
  contradictions with an existing project, unsolicited advice, unnecessary repetition, tone-based
  invalidation, belief mirroring, and inference presented as fact. Non-streaming replies receive one
  bounded repair attempt for material semantic-fidelity findings.

### Verification

- Focused backend communication, profile, audit, prompt, memory-authorization, and API tests passed.
- Backend Ruff checks passed.

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
