# Project Master — ALPHA v0.1.1

This is the first self-contained Project Master Windows alpha. The desktop interface now packages
and manages the existing Python AI engine instead of requiring users to start `master serve` in a
separate terminal.

## Fixed from v0.1.0

- Bundled the Python engine in both Windows installers.
- Start the local API automatically when Project Master opens.
- Store the database, configuration, workspace, and backend log in the user's application-data
  directory.
- Stop the full backend process tree when Project Master exits.
- Detect a crashed backend, show a clear inline failure, and restart it through one Retry action.
- Preserve the CLI, Ollama integration, tool calling, memory, claims/evidence, adaptive style, and
  response auditing.

## Install

1. Install and start [Ollama](https://ollama.com/download/windows).
2. Install at least one chat model, for example `ollama pull qwen3:8b`.
3. Download and run `Project-Master-ALPHA-v0.1.1-x64-setup.exe`.
4. Open Project Master from the Start menu.

Python, Node.js, Rust, and a separate backend terminal are not required for the installed app.

## Verification

- Python tests: 20 passed
- Python lint: passed
- Packaged sidecar smoke test: passed
- Frontend production build: passed
- Rust lifecycle tests: 4 passed
- Rust formatting and clippy: passed
- NSIS and MSI installer builds: passed
- Silent in-place upgrade from v0.1.0: passed
- Installed backend auto-start and clean shutdown: passed
- Forced backend crash → inline error → one Retry → new backend → completed chat: passed

## Alpha limitations

- Ollama and at least one compatible local model are still required.
- The Windows installers are not code-signed and may trigger a Microsoft reputation warning.
- Tool activity, memory, evidence, settings, and conversation-selection interfaces are not built
  yet, although the Python engine retains those capabilities.
- The default context is 32768. An 8192 context is a safer starting point on many 8 GB GPUs.
- Expect breaking changes and database migrations in later alpha versions.

## Creator

Created by [Master Mike](https://linktr.ee/realmastermike). Follow the build on
[YouTube](https://www.youtube.com/@RealMasterMike?sub_confirmation=1), see more work on
[GitHub](https://github.com/RealMasterMike), or optionally support development through
[Streamlabs](https://streamlabs.com/mastermike/tip).
