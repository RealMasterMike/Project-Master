# Project Master v0.3.0 Linux handoff

Last updated: 2026-07-27  
Repository: `/home/mike/Project-Master`  
Target: Fedora Linux daily-driver beta  
Release state: **SHIPPED — v0.3.0 published 2026-07-27 with Mike's explicit authorization**  
Release URL: https://github.com/RealMasterMike/Project-Master/releases/tag/v0.3.0

## Release record

v0.3.0 shipped on 2026-07-27: Mike said "ship it" after his supervised GUI pass. Commit `b6716e9`
on `main`, annotated tag `v0.3.0`, GitHub pre-release with the accepted AppImage, unsigned RPM,
and SHA-256 manifest. GitHub stores the RPM asset as `Project.Master-0.3.0-1.x86_64.rpm` (space
became a period); its hash matches the manifest.

The shipped candidate contains, beyond the original v0.3.0 scope: the thinking-mode prompt
policy, the Linux `WEBKIT_DISABLE_DMABUF_RENDERER=1` launcher fix (blank window on this hybrid
Intel/NVIDIA Wayland machine), the Mission view for Team runs with a Mission/Transcript toggle,
black dropdown-option text (WebKitGTK light popup), a truthful interactive-inference busy message
with a 15 s lease wait, and the Linux quit process-tree fix that stops the PyInstaller sidecar
from orphaning the real server on port 8765. Its headless gate passed **27/27 checks** with
**12/12 physical Ollama models**.

Next: Mike daily-drives the released build; findings feed v0.3.1 (see `ideas.md`, including
inference-settings drop-downs). Signing/updater provisioning and Windows validation remain open
follow-ups.

## Read this first

- Project Master and its packaged backend are currently closed.
- Mike explicitly asked that the desktop remain closed while it is being changed or tested. Tell him
  before a final GUI launch and close it immediately afterward.
- Do not commit, push, tag, create a GitHub Release, or upload artifacts until Mike completes his
  manual acceptance pass and explicitly approves shipping.
- Do not install the RPM with `sudo` unless Mike is present and authorizes it. A previous attempt
  stopped at the password prompt without entering a credential.
- Preserve the dirty worktree. It contains the complete v0.3.0 implementation and documentation;
  do not reset, checkout, clean, or overwrite it.
- `ideas.md` and `approvals.md` are product-planning records. Only Mike may change a proposal to
  `APPROVED`.

## Source state

- Branch: `main`
- Base commit: `4e44a98d5141519e165d88bd439ed84791b056d6`
- Base is still aligned with `origin/main`.
- The v0.3.0 work is intentionally uncommitted: many tracked files are modified and many new files
  are untracked.
- `git diff --check` currently passes.
- All package/runtime version sources are aligned at `0.3.0`.
- Desktop identity: `Project Master — BETA v0.3.0 RC`
- Tauri identifier: `com.master.desktop`

## Implemented v0.3.0 scope

### Multi-AI core

- Discovers Ollama models locally.
- De-duplicates multiple tags that point to the same physical model.
- Mike currently has 13 tags representing 12 physical models.
- Team mode assigns a lead and bounded specialist roles, executes sequentially to protect VRAM, and
  returns one accountable synthesis.
- Direct mode remains available for faster single-model work.
- The lead owns the tool loop; specialist drafts are not exposed as chain-of-thought.
- Team activity, roles, failures, tools, runs, events, cancellation, and delivery state are surfaced
  in the desktop.

### Projects, Binder, and tools

- Durable projects, runs, events, artifacts, approvals, and job ledgers.
- Local Project Binder indexing with SQLite FTS5, source versions, SHA-256 provenance, secret-file
  exclusions, bounded excerpts, and citations.
- Binder context works in Direct and Team chat.
- Unified tools for calculator, time, workspace, memory, evidence, terminal, Binder, Dream,
  ComfyUI, and Voice Studio.
- Chat is read-only by default.
- Mutating tool schemas are hidden and execution is rejected unless the current request explicitly
  sets `allow_mutations`.
- Consecutive duplicate tool calls are suppressed before repeating a side effect.
- Linux terminal execution is argument-only, bounded, network-disabled by default, and uses
  Bubblewrap when available. The fallback is a small read-only allowlist.

### Dream Lab

- Built-in and custom Dream recipes.
- Manual and scheduled council runs.
- Explicit source snapshots and provenance.
- Project, memory, mixed, wildcard, and all-source schedule resolution.
- Per-project Dream consent and indexed-Binder readiness checks.
- Resource, quiet-window, catch-up, recovery, cancellation, and revoked-consent handling.
- Outputs are proposal-only Inbox items and never promote themselves.

### ComfyUI Creator

- ComfyUI is not installed on this machine, but support is implemented.
- Local and explicitly trusted remote profiles.
- Immutable API-format workflow revisions and exact-digest approval.
- Typed/bounded bindings, owned jobs, refresh, cancellation, restart reconciliation, and retry.
- Verified local artifacts with safe filenames, MIME/size checks, SHA-256 provenance, authenticated
  downloads, and image/audio/video previews.
- ComfyUI being offline is a supported state and must not block startup.

### Voice Studio

- eSpeak NG designed voices, pronunciation controls, chunking, normalization, jobs, caching, and
  verified artifacts.
- Optional Chatterbox reference voices with user rights attestation.
- Chatterbox is already installed under:
  `/home/mike/.local/share/com.master.desktop/voice-engines/chatterbox`
- The Chatterbox installation is approximately 11 GB and contains pinned source/model revisions and
  a SHA-256 asset inventory. Do not reinstall or redownload it.
- The final frozen backend reports:
  - eSpeak NG `1.52.0`: `ready`
  - Chatterbox `0.1.7+git.5de7a54aa4e5`: `ready on cuda`
  - Six pinned Chatterbox assets registered

### Desktop and packaging

- Tauri owns the backend lifecycle and generates a new authenticated loopback session token for
  every launch.
- The API rejects requests without the current token.
- CSP, recovery, shutdown, and port-release behavior are implemented.
- Fedora-safe AppImage and RPM packaging works around current RELR/linuxdeploy incompatibilities.
- The local build preserves distinct RPM/AppImage bundle markers, validates the full AppDir, and
  verifies produced artifacts.
- Windows build support remains in source but has not been validated from this exact v0.3.0 state.

## Late blockers found and fixed

Two real Chatterbox bugs were discovered during exact packaged-backend acceptance:

1. AppImage/PyInstaller variables such as `PYTHONHOME`, `PYTHONPATH`, and packaged
   `LD_LIBRARY_PATH` leaked into the separately managed Chatterbox virtual environment. The worker
   process environment is now sanitized before external Python and FFmpeg are launched.
2. The adapter resolved `venv/bin/python` to its base interpreter, which silently removed the
   virtual environment's packages. The adapter now preserves the virtual-environment launcher path.

The focused Chatterbox regression suite passes **8/8**, and the final staged AppImage backend
registered Chatterbox, reported CUDA-ready health, and rendered a checksum-verified isolated WAV.

The first complete model gate then exposed two Ollama behaviors:

1. Ollama's default five-minute retention kept three prior runners resident. Loading the 15.63 GiB
   26B GGUF on top of them exhausted RAM and swap, and the Linux OOM killer terminated
   `ollama.service`.
2. The same 26B conversion emitted only a private `thinking` field even though `/api/show` did not
   advertise the `thinking` capability.

Project Master's Ollama clients now share a process-local residency coordinator, unload the prior
Project Master runner before a model switch, serialize streams through that transition, and fail
closed when unloading cannot be confirmed. GPU Chatterbox startup also unloads Project Master's
warm Ollama model after acquiring the shared GPU lease, and normal backend shutdown unloads the
last Project Master runner before a later relaunch. Requests defensively send `think: false` to
non-GPT-OSS models even when metadata omits thinking; GPT-OSS receives its lowest supported level.
The acceptance harness independently enforces a catalog-model unload barrier after every physical
model and requires exclusive Ollama use because the daemon cannot attribute a same-tag runner to a
particular local client.

## Current exact local artifacts

These hashes were measured after the final 2026-07-27 rebuild (Mission view, dropdown fix, busy
message, quit process-tree fix). They supersede the earlier `9a768eb0…` and `791390e0…` AppImage
candidates and their acceptance evidence:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `src-tauri/target/release/bundle/appimage/Project-Master-0.3.0-x86_64.AppImage` | 140843512 | `07dfbcb5d6431648c248f6e785c8909be257bb9473c64ac4610baff9c1552012` |
| `src-tauri/target/release/bundle/rpm/Project Master-0.3.0-1.x86_64.rpm` | 28218407 | `055db1a24860a558b88f77e73ecb80149a75f61f8d4416b689cd7e24b7b02128` |
| `src-tauri/binaries/project-master-backend-x86_64-unknown-linux-gnu` | 21331504 | `b11ec961eecaa2ba6180246c18432f4ed948d1ad892c18ab576d78b3496dcec2` |
| AppDir packaged backend | 21335600 | `dcd4114dd598382b12754440483a93434ea8e8923f3327134f0340ae9ceac0d8` |

The build-generated manifest
`release/local/Project-Master-0.3.0-linux-x64-SHA256SUMS.txt` contains local repository-relative
AppImage/RPM paths and hashes. It is build evidence, not an upload asset. Only the basename-only
`release/local/Project-Master-0.3.0-linux-x86_64-SHA256SUMS.txt` is the release manifest;
`CHANGELOG.md` is aligned to the same candidate.

## Verification already completed

All of the following were re-run against the current (post-DMABUF-fix) candidate on 2026-07-27:

- Complete backend suite after all resource-management patches: **264 passed, 1 skipped**
- Backend Ruff lint: passed
- Frontend tests: **31/31 passed**
- Production TypeScript/Vite build: passed, 299 modules
- Rust formatting: passed
- Strict Rust Clippy: passed
- Rust native tests: **5/5 passed**
- Packaging regression suite: passed
- Frozen sidecar smoke: passed
- Final RPM/AppImage build and package verification: passed
- Exact staged AppDir gate: **27/27 checks passed** in 463,898 ms with the GUI closed
- Ollama catalog: 13 tags de-duplicated to **12/12 physical models passed**
- 26B SuperGemma: passed the packaged Direct response in 28,325 ms, then unloaded cleanly
- Headless GUI-parity tests against the packaged backend: Team council run completed with a
  correct synthesized answer and recorded run ID; mid-stream Direct cancellation emitted a clean
  `cancelled` event with no completed reply; both cleaned up their Ollama runners
- Voice: eSpeak and Chatterbox CUDA health/render and artifact checks passed
- Lifecycle: both authenticated backend phases stopped with SIGTERM, released their ports, removed
  the isolated profile, and left no backend or Ollama runner resident
- `git diff --check`: passed

Canonical evidence:

- `release/local/Project-Master-0.3.0-linux-x86_64-acceptance.json`
  (`f246c90fa443ea261c311a15ab65759839345f92ddce3f2c74c60c0807f569c4`)
- `release/local/Project-Master-0.3.0-linux-x86_64-acceptance.md`
  (`84c56c6f9c8c2842a71d9efb75af5a9469a7678ce6a962808fb125c71c7c40f6`)
- The first failed/OOM diagnostic reports were retained with
  `-acceptance-failed-oom-20260727` filenames.

Important distinction: the exact candidate has passed the complete headless gate but has not
received Mike's GUI acceptance pass.

## Remaining release blockers

1. Give Mike the closed AppImage and let him complete the short manual daily-driver pass below.
2. Review and intentionally commit the dirty worktree only after that pass.
3. Validate Windows installers against this exact source state if Windows is part of v0.3.0.
4. Provision signing and beta-updater publishing only if Mike wants them for this release.
5. Create the tag and GitHub Release only after Mike explicitly says to ship.

## Agreed completion plan

1. **Completed:** scope freeze, reproducible acceptance harness, blocker batch, rebuild, full source
   regressions, exact packaged gate, and final checksum freeze.
2. **Next:** Mike runs the concise manual checklist against the closed AppImage.
3. **After explicit approval only:** review/commit the dirty worktree, prepare signing/updater
   metadata as requested, tag, and publish.

## Accepted headless gate

The implemented harness:

- Run the exact backend staged inside `master.AppDir`, not a source-only server.
- Use an isolated temporary database and workspace.
- Never print or persist a real desktop session token.
- Reject unauthenticated access with HTTP 401.
- Assert API/package version `0.3.0`.
- Assert Ollama is reachable and account for all physical models once; aliases sharing a digest are
  intentionally de-duplicated.
- Put a strict timeout around each model and record success, skip reason, or failure rather than
  hanging indefinitely.
- Verify safe tools first, then test one denied and one explicitly authorized workspace mutation.
- Index a small Binder fixture and verify an exact codeword and citation.
- Create only disabled/model-free Dream schedule fixtures unless the model council phase is running.
- Treat ComfyUI offline as a passing supported state.
- Test eSpeak in the isolated profile.
- Test the already-installed Chatterbox engine without downloading anything.
- Terminate the backend in `finally`, verify its port closes, and write a JSON/Markdown report.
- Leave the desktop GUI closed.

The final run satisfied every item above. Its isolated profile was removed after verified shutdown.
Do not rebuild the candidate merely to repeat a source test; rebuilding changes the accepted hashes.

## Mike's supervised GUI checklist

1. Ensure no other Ollama client is active, then launch the exact accepted AppImage.
2. Select an installed model because `qwen3:8b` is absent; verify one Direct reply, one Team reply,
   and Stop/cancellation.
3. Create and reopen a disposable Project, index and attach its Binder, and confirm a cited answer.
4. Confirm project changes stay denied by default, then enable the per-chat mutation toggle for one
   disposable edit.
5. Run one manual Dream and confirm its output remains a pending proposal.
6. Render and download one eSpeak artifact and, if desired, one Chatterbox reference artifact.
7. Confirm the offline ComfyUI state does not block the app, then quit, relaunch once to verify
   persisted state/recovery, and quit again.

## Useful commands

From `/home/mike/Project-Master`:

```bash
# Frontend
npm test
npm run build
npm run packaging:test

# Backend
cd ProjectMaster-v0.1.0
.venv-linux/bin/python -m pytest
.venv-linux/bin/ruff check src tests
cd ..

# Frozen sidecar
npm run backend:sidecar:test

# Linux packages
npm run tauri:build:linux:local
npm run packaging:verify

# Whitespace and exact hashes
git diff --check
sha256sum \
  "src-tauri/target/release/bundle/appimage/Project-Master-0.3.0-x86_64.AppImage" \
  "src-tauri/target/release/bundle/rpm/Project Master-0.3.0-1.x86_64.rpm"
```

Do not rebuild merely to rerun a test. Any rebuild changes artifact hashes and invalidates prior
artifact acceptance evidence.

## Operational notes

- App data: `/home/mike/.local/share/com.master.desktop`
- Backend log: `/home/mike/.local/share/com.master.desktop/backend.log`
- Real database: `/home/mike/.local/share/com.master.desktop/master.db`
- Chatterbox engine: `/home/mike/.local/share/com.master.desktop/voice-engines/chatterbox`
- ComfyUI is currently absent; do not install it just to pass v0.3.0 acceptance.
- The configured backend default still names `qwen3:8b`, which is not currently installed. The
  desktop is intended to select an available conversational model, but exact first-run selection
  remains part of Mike's acceptance gate. Do not download `qwen3:8b` without his direction.
- The 26B model is confirmed usable on this 30 GiB RAM / 8 GiB swap machine when isolated. The first
  OOM was runner accumulation, not proof that the model cannot run.
- Project Master tracks only requests made through its own shared Ollama client family, but Ollama
  unloads by model tag and cannot attribute a same-tag runner to one application. Use Project Master
  and the release harness with exclusive Ollama access; noncatalog foreign tags are left untouched.
- Current thinking policy is deliberately conservative: non-GPT-OSS requests disable thinking,
  including converted models whose metadata omits it; GPT-OSS uses `low`. Precise per-model
  thinking-mode detection and controls remain a documented follow-up in `ideas.md`.
- This machine (hybrid Intel Arrow Lake + NVIDIA RTX 5070 on Wayland) rendered a blank window with
  `Failed to create GBM buffer` under WebKitGTK's DMA-BUF renderer. The desktop now sets
  `WEBKIT_DISABLE_DMABUF_RENDERER=1` itself on Linux unless the variable is already set, so no
  launch wrapper is needed.
- ImageMagick desktop capture failed twice with `missing an image filename` despite valid syntax.
  Do not repeat those unchanged commands. `wmctrl` and `xdotool` are not installed.
- The RPM is unsigned and intended only for local testing. The AppImage is the no-install candidate.
- Normal candidate use must remain local-first. No data, prompts, Binder files, voices, or tokens
  should leave the machine.

## Documentation authority

- `ProjectMaster-v0.1.0/ROADMAP.md` — current implementation and release-engineering scope
- `CHANGELOG.md` — release claims and mandatory release gate
- `ideas.md` — non-binding product ideas
- `approvals.md` — Mike-controlled product decisions
- `DESIGN_BRIEF.md` — historical original MVP brief, superseded for current scope
- `docs/LINUX_PACKAGING.md` — Fedora build and acceptance guidance

The next action is Mike's manual GUI pass. Tell him before launching, keep the GUI open only for
that supervised pass, close it immediately afterward, and stop before any commit or public action.
