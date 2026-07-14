# Changelog

All notable Project Master desktop releases are recorded here.

## Release gate

No build may be uploaded or published unless all of the following are complete:

- Add a versioned entry to this changelog.
- Add version-matched release notes describing changes, fixes, known issues, and verification.
- Build and test the exact artifacts intended for upload.
- Generate checksums after the final artifacts are produced.
- Confirm the changelog, release notes, artifact filenames, application version, tag, and checksums
  all use the same version.

If any item is missing, the release is blocked. Finish the release documentation before uploading
another build.

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
