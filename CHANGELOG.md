# Changelog

All notable Project Master desktop releases are recorded here.

## 0.3.0 BETA RC — 2026-07-27

This entry describes the Linux daily-driver release candidate. It does not mean the candidate has
been uploaded or approved for public release; the release gate below still applies.

### Added

- Added an obvious multi-AI **Team** mode backed by the installed Ollama catalog. Project Master
  de-duplicates aliases for the same physical model, selects a capable lead, assigns bounded
  specialist roles, executes the council sequentially, and streams a user-facing Team Strip and
  Run Rail without exposing specialist drafts as chain-of-thought.
- Added durable **Projects** with local roots, run and artifact ledgers, and safe event summaries.
- Added the **Project Binder** local RAG path: bounded text/source indexing, secret and credential
  exclusions, content versioning and SHA-256 provenance, cited search results, and attachable
  retrieval context for Direct and Team chat.
- Added a unified tool inventory covering workspace inspection, calculator, time, memory, evidence,
  bounded terminal commands, Project Binder, Dream, ComfyUI, and Voice Studio operations.
- Added **Dream Lab** with explicit-source manual runs, custom recipes, provenance snapshots,
  resource-aware schedules, bounded catch-up, quiet hours, and a proposal-only Inbox requiring a
  human accept/reject decision. Scheduled Project Binder sources require explicit per-project
  consent, an indexed source, and a scoped recipe before a schedule can be enabled.
- Added **ComfyUI Creator** support for trusted connection profiles, immutable API-format workflow
  revisions, typed and bounded input bindings, exact-revision approval, owned jobs, cancellation,
  restart reconciliation, and checksum/provenance-preserving local output artifacts. The desktop
  gallery previews image, audio, and video outputs and prepares authenticated local downloads.
- Added **Voice Studio** with designed and attested reference-voice profiles, script projects,
  cancellable chunked jobs, the distribution eSpeak NG adapter, optional pinned Chatterbox setup,
  and checksum-verified downloadable audio artifacts.
- Added first-class Fedora packaging through host-native Python sidecars, Tauri RPM and AppImage
  targets, cross-platform Node build launchers, prerequisite checks, sidecar smoke tests, artifact
  verification, and local SHA-256 manifests.
- Added one reproducible headless Linux candidate gate that runs the exact backend staged in
  `master.AppDir` through isolated authentication/lifecycle, Ollama, tools, Binder, mutation,
  Dream, ComfyUI-offline, eSpeak, Chatterbox, and per-physical-model checks, then emits JSON and
  Markdown reports without launching the GUI or persisting a session token.
- Added dashboard workspaces for Projects, Approval Center, Dream, Creator, and Voice while
  preserving streaming chat, conversation history, layout customization, cancellation, and
  backend recovery.
- Added a **Mission view** for the center pane during Team runs: goal, live status with a phase
  progress bar, specialist completion counts, council decisions, tool activity, and the delivered
  synthesis as a mission document instead of chat bubbles. A Mission/Transcript toggle preserves
  the conversation view, and the mission surface renders only the same bounded run events the Run
  Rail already exposes — never specialist drafts or private reasoning.

### Changed

- Repositioned Project Master from a single-agent Windows alpha into a local-first multi-AI beta
  candidate while retaining Direct mode and the Windows build path. Windows v0.3.0 installers still
  require validation against the same accepted source state.
- Changed the default desktop experience to Team mode when a compatible local council is available.
- Changed release metadata across npm, Python, Cargo, Tauri, the browser title, and the desktop
  window to `0.3.0`.
- Changed beta update checks to the weekly policy and moved the configured updater endpoint away
  from the alpha channel. A signed beta channel still has to be created before public release.
- Changed Python packaging to bounded dependency versions verified across supported Python 3.11
  through 3.14 runtimes.
- Changed failed-chat retries to use the mutation authorization currently shown in the desktop
  instead of silently reusing consent captured by an earlier request.
- Changed Ollama requests to defensively disable private thinking for non-GPT-OSS models even when
  converted-model metadata omits the capability, and select GPT-OSS's lowest supported level,
  preventing hidden reasoning from silently consuming the entire bounded visible-response budget.
- Added switch-aware Ollama residency: Direct tool rounds can keep one model warm, while Team,
  Dream, and model changes explicitly unload Project Master's prior runner before loading another
  and fail closed if that isolation cannot be confirmed. Starting GPU Chatterbox synthesis also
  unloads Project Master's warm Ollama runner after acquiring the shared GPU lease, and normal
  backend shutdown unloads the final active runner before a later relaunch.
- Added a truthful `synthetic_reference` voice-rights basis for generated reference audio and
  stopped substituting the reference WAV itself as consent or license evidence.
- Changed dashboard Customize actions to return to Command with the requested panel visible,
  wrapped compact-window header controls, and exposed the explicit trusted-host list required to
  save a remote ComfyUI profile.
- Changed the Fedora local package build to compile once, preserve the correct RPM/AppImage bundle
  markers, validate the fully deployed AppDir, bypass the obsolete linuxdeploy RELR strip failure,
  and produce one branded, versioned AppImage before checksum verification.
- Added a thinking-mode selection policy to the system prompt's communication rules: start at
  Medium, use the lowest level that fits the task, and reserve High/Maximum for work that genuinely
  benefits from deeper analysis instead of treating reasoning depth as a quality scale.
- The Linux desktop now disables WebKitGTK's DMA-BUF renderer at launch unless the user has
  explicitly set `WEBKIT_DISABLE_DMABUF_RENDERER` themselves. Hybrid Intel/NVIDIA Wayland systems
  otherwise render a blank window with repeated "Failed to create GBM buffer" errors.
- Changed the interactive-inference busy error to stop blaming the background Dream worker: a
  request that arrives while a just-cancelled run is still winding down now reports that local
  models are finishing the previous request and suggests retrying, and the lease wait grew from
  10 to 15 seconds so quick resends after Stop usually succeed instead of erroring.
- Fixed Linux desktop quit orphaning the packaged backend: the PyInstaller sidecar forks the real
  server process, and killing only the launcher left that server holding port 8765 with the old
  session token, which would have blocked the next launch. Quit now collects the sidecar's
  descendant processes, stops the launcher, and terminates the descendants so uvicorn shuts down
  cleanly and releases the port. Discovered during Mike's supervised GUI pass.
- Fixed unreadable drop-down menus on Linux: WebKitGTK draws `<select>` popups on a native light
  background, so inherited near-white option text was invisible. Options are now black on white
  with grey disabled entries.

### Security

- Chat tools are read-only by default. Every mutating tool is hidden from model schemas and rejected
  at execution unless Mike explicitly enables **Allow project changes**. The desktop toggle
  applies only to the current live conversation and resets on app launch or conversation change;
  authorization is independently scoped to each backend request and works the same way in Direct,
  Team, sync, and streaming paths.
- Consecutive duplicate tool calls are suppressed before they can repeat a side effect. Project
  Master then requests one tool-free final response and preserves a truthful partial result if the
  model still cannot finish.
- Workspace file tools remain inside the active project root. Linux terminal calls accept argument
  arrays rather than shell strings, enforce time/resource/output bounds, use Bubblewrap for
  project-bound execution when available, and keep network access separately disabled by default.
  The no-Bubblewrap fallback is an explicitly read-only command allowlist rather than a filesystem
  sandbox.
- Dream outputs remain proposals and never promote themselves. Product proposals in `ideas.md` and
  `approvals.md` remain separate from runtime Approval Center decisions.
- ComfyUI defaults to loopback, requires explicit HTTPS host trust for remote profiles, refuses
  cross-origin redirects, executes only approved workflow digests, and validates filenames, paths,
  sizes, MIME types, and hashes before persisting outputs.
- Voice references stay in app-owned storage, require an explicit rights attestation before profile
  use, and produce checksum-verified artifacts. Synthetic references remain explicitly distinct
  from real-person voices, and consent/license modes require separate evidence. Normal app startup
  does not execute voice-engine installers or download model assets.

### Known issues

- Ollama and at least one compatible conversational model are still required for chat and Dream.
  Local model quality determines tool-call reliability.
- This machine does not have the configured `qwen3:8b` default. The first GUI pass must select an
  installed model; the release process must not silently download that model.
- Team mode is intentionally sequential and can take longer when many compatible models are
  installed. This protects machines with limited VRAM but is not low-latency fan-out.
- Project Master unloads only runners used by its own shared Ollama clients. Models loaded by a
  different local application remain outside that ownership boundary and can still compete for RAM.
- GPT-OSS does not support disabling thinking; Project Master requests its lowest supported level.
  Full per-model thinking-mode discovery and user controls remain a post-v0.3 follow-up.
- The desktop enables self-voice and synthetic/generated reference classifications. Explicit
  consent and licensed-voice records need a future evidence-document upload and verification
  registry; those choices remain disabled in the desktop rather than treating an unchecked
  identifier as verified evidence.
- Scheduled Dream work runs only while the application/backend is running. Project sources require
  explicit consent, an indexed Binder, and recipe scopes; memory sources remain an advanced
  metadata-driven path.
- ComfyUI and Chatterbox are optional, separately installed integrations. ComfyUI being unavailable
  must not block application startup; Chatterbox setup is a large explicit download and currently
  targets the pinned Linux/PyTorch stack documented in `docs/LINUX_PACKAGING.md`.
- Project Binder uses local lexical SQLite search rather than embeddings or a vector database.
- eSpeak NG is a reliable lightweight fallback, not neural voice cloning.
- The source engine directory retains the legacy name `ProjectMaster-v0.1.0`; package and runtime
  versions are nevertheless `0.3.0`.
- The signed beta updater channel and beta publishing automation are not yet provisioned. Install
  and upgrade this candidate manually until version-matched signed update artifacts exist.
- `RC` is currently a display label while every package already uses plain semantic version
  `0.3.0`. The public release must therefore ship the exact accepted candidate bits or advance the
  package version; a different same-version final would not be a valid updater upgrade.

### Verification

- The portable packaging test and Fedora prerequisite preflight passed with every package, Python,
  Cargo, Tauri, and runtime version source aligned at `0.3.0`.
- All five updater-policy tests passed, Cargo metadata reported `master 0.3.0`, the Python package
  imported as `0.3.0`, and the release-metadata diff passed whitespace validation.
- The complete Python suite passed with 264 tests and one intentional skip; Ruff passed. The
  frontend passed all 31 tests and its production TypeScript/Vite build (299 modules).
- Rust formatting, strict Clippy, and all five native lifecycle/version tests passed.
- The frozen 0.3.0 Python sidecar passed authenticated startup, unauthenticated rejection,
  Ollama-offline truthfulness, tool diagnostics, database creation, shutdown, and port-release
  checks. Its PyInstaller archive contains the packaged Chatterbox worker resource.
- The exact unsigned local AppImage and RPM passed SHA-256 verification. The AppImage hash is
  `07dfbcb5d6431648c248f6e785c8909be257bb9473c64ac4610baff9c1552012`; the RPM hash is
  `055db1a24860a558b88f77e73ecb80149a75f61f8d4416b689cd7e24b7b02128`.
- The exact AppDir backend passed the reproducible headless Fedora gate in 463,898 ms with the GUI
  closed: all 27 checks passed, all 13 Ollama tags were accounted for as 12 physical models, the 26B
  SuperGemma completed in 28,325 ms, both voice engines rendered checksum-verified WAVs, and final
  cleanup left no Ollama or backend process resident. The RPM remains uninstalled and Mike's short
  GUI daily-driver pass is still required before public release.
- Mike explicitly authorized shipping on 2026-07-27 after his supervised GUI pass surfaced and
  verified fixes on the final candidate. The v0.3.0 tag and GitHub pre-release were published with
  the accepted AppImage, unsigned RPM, and SHA-256 manifest. GitHub renames the RPM asset's space
  to a period (`Project.Master-0.3.0-1.x86_64.rpm`); its hash matches the manifest entry.

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
