# Linux daily-driver build and packaging

Project Master builds the Python backend as a host-native Tauri sidecar, then packages the
desktop application as an RPM and AppImage. These commands do not install ComfyUI, Ollama
models, voice engines, or other optional integrations.

This guide targets the **v0.3.0 Beta release candidate** on Fedora x86-64. It prepares and verifies
local artifacts only; none of these commands commit, tag, upload, or publish a release.

## Fedora prerequisites

Install the native build toolchain once:

```bash
sudo dnf install \
  webkit2gtk4.1-devel \
  gtk3-devel \
  libappindicator-gtk3-devel \
  librsvg2-devel \
  dbus-devel \
  pkgconf-pkg-config \
  openssl-devel \
  patchelf \
  rpm-build \
  fuse \
  fuse-libs
```

Install Rust with rustup and keep a supported Python 3.11 through 3.14 available. The build
launchers detect `~/.cargo/bin` even when the current shell has not sourced Cargo's environment.
Node.js 20 or newer and npm are also required. Follow the official Tauri 2 prerequisites if a
different Fedora release uses renamed compatibility packages.

Install JavaScript dependencies and inspect the machine without changing it:

```bash
npm ci
npm run packaging:test
npm run packaging:check
```

`packaging:check` prints every missing prerequisite and exits unsuccessfully until the machine is
ready.

## Daily-driver runtime prerequisites

Install and start Ollama separately, then inspect the installed conversational models:

```bash
ollama serve
ollama list
```

Project Master inspects installed model identities for Team mode, but automatic Direct, Team, and
Dream selection is limited to exact curated tags whose Ollama manifest digest matches a physically
tested identity and whose curated purpose matches that path. Execution admission refreshes the
catalog so a replaced mutable tag is not trusted from stale status data. Other installed tags
remain manual/unverified and do not silently join an automatic council. Tool-calling support is
recommended for a deliberately selected lead; the application cannot invent tool calls a model
does not produce. Pull any model only as an explicit setup decision.

Project Master's model clients share one residency coordinator: switching models unloads the prior
Project Master runner before loading the next, and GPU Chatterbox startup unloads Project Master's
warm runner after acquiring the shared GPU lease. This prevents the multi-runner RAM accumulation
that previously caused Linux to OOM-kill Ollama while loading the 26B SuperGemma. Normal backend
shutdown also unloads the final Project Master runner before relaunch. Ollama's tag-scoped unload
API cannot distinguish two applications using the same tag, so do not concurrently use another
Ollama client with Project Master on a memory-constrained machine.

Ollama and ComfyUI also use a bounded GPU handoff. Before interactive Ollama use, Project Master
waits for every reachable configured ComfyUI queue to become idle and then calls the official
ComfyUI `POST /free` endpoint with both model unloading and memory release enabled. Active ComfyUI
queues stay on the existing local-model busy path; offline optional profiles do not prevent chat
from starting. Before Project Master submits a ComfyUI workflow, it unloads only the Ollama runner
tracked by its own client family. This is cache coordination, not ownership of prompts submitted
by other ComfyUI clients.

Optional Fedora packages improve local creator features:

```bash
sudo dnf install espeak-ng ffmpeg bubblewrap
```

- `espeak-ng` and `ffmpeg` enable the fast local Voice Studio fallback.
- `bubblewrap` enables the bounded Linux workspace terminal sandbox. Without it, Project Master
  falls back to a small read-only command allowlist.
- ComfyUI and Chatterbox are optional and are installed separately below.

## Build and verify the sidecar

Before freezing a package candidate, verify the complete source state:

```bash
cd ProjectMaster-v0.1.0
.venv-linux/bin/python -m pytest
.venv-linux/bin/ruff check src tests
cd ..
npm test -- --run
npm run build
cargo fmt --manifest-path src-tauri/Cargo.toml -- --check
cargo clippy \
  --manifest-path src-tauri/Cargo.toml \
  --all-targets --all-features -- -D warnings
cargo test --manifest-path src-tauri/Cargo.toml
```

The default build creates an isolated environment at
`ProjectMaster-v0.1.0/.venv-packaging` and installs the backend's `packaging` extra:

```bash
npm run backend:sidecar
npm run backend:sidecar:test
```

To choose Python explicitly:

```bash
PROJECT_MASTER_PACKAGING_PYTHON=/usr/bin/python3.12 npm run backend:sidecar
```

For an offline developer smoke build, an already prepared environment may be reused explicitly:

```bash
node scripts/build-backend-sidecar.mjs \
  --python ProjectMaster-v0.1.0/.venv-linux/bin/python \
  --reuse-current-environment
```

Release builds should use the isolated default rather than a developer environment.

## Build Fedora artifacts

```bash
npm run tauri:build:linux:local
npm run packaging:verify
```

The build requests only the Linux RPM and AppImage bundles. Outputs are created below:

- `src-tauri/target/release/bundle/rpm/`
- `src-tauri/target/release/bundle/appimage/`

The local launcher performs one sidecar, frontend, and Rust build, then bundles RPM and AppImage
from separate copies of the pristine executable so Tauri records the correct package-type marker
in each. On Fedora toolchains that emit RELR ELF sections, it validates the fully deployed AppDir
and uses Tauri's cached AppImage output plugin without relying on linuxdeploy's obsolete strip
implementation. Cache and repository paths are resolved at runtime; the final image is named
`Project-Master-<version>-<architecture>.AppImage`.

The `:local` command disables Tauri updater artifacts so unsigned dogfood builds do not require
release signing credentials. The verification command checks that each artifact is non-empty and
writes a local checksum manifest below `release/local/`. It also rejects an artifact set whose
package, Python, Cargo, or Tauri versions do not all match `0.3.0`. Nothing is uploaded or
published.

For the exact signed release build, configure the Tauri signing credentials first and use
`npm run tauri:build:linux`. Do not substitute an unsigned local artifact after acceptance testing;
verify the exact artifacts intended for upload again.

## Install the candidate as a daily driver

Install the RPM through DNF so Fedora can validate and manage its dependencies:

```bash
sudo dnf install ./src-tauri/target/release/bundle/rpm/*.rpm
```

Then launch **Project Master** from the desktop application menu. Installing a later build with the
same command upgrades the managed RPM; keep the generated checksum manifest with the artifact you
accepted.

Run the AppImage directly for Mike's acceptance test:

```bash
chmod +x src-tauri/target/release/bundle/appimage/*.AppImage
./src-tauri/target/release/bundle/appimage/*.AppImage
```

The application expects Ollama at its configured loopback URL, but the packaged backend must
start and expose its API even when Ollama is unavailable.

The packaged app owns its Python sidecar lifecycle. Configuration, `master.db`, the managed
workspace, artifacts, optional voice engines, and `backend.log` stay below Tauri's per-user
application-data directory rather than beside the RPM or AppImage. Startup errors report the exact
backend log path. Do not run a second `master serve` against port `8765` at the same time.

## Optional ComfyUI connection

Project Master does not download or start ComfyUI. Install it through the method you trust and start
it separately, normally on a loopback address. In Project Master's **Creator** workspace:

1. Save a connection profile for the loopback endpoint.
2. Export a ComfyUI graph with **Save (API Format)**.
3. Import the JSON and define only the typed values Mike should be able to change.
4. Review the immutable workflow digest and explicitly approve that revision.
5. Queue the approved workflow and inspect its owned job status.
6. Open each verified artifact in the Creator gallery, preview supported image/audio/video output,
   and prepare an authenticated local download.

Remote ComfyUI profiles are not equivalent to loopback. They require explicit host trust and HTTPS,
and the backend refuses redirects to another origin. Workflow approval applies to one exact digest;
editing and re-importing a workflow creates a new pending revision.

## Optional voice engines

For the distribution eSpeak NG fallback, the Fedora packages above are sufficient. Voice Studio
discovers the executable, normalizes output through `ffmpeg`, and stores checksum-verified audio in
the app-owned artifact directory.

Chatterbox provides higher-quality local reference-voice rendering and requires Python 3.11:

```bash
bash scripts/setup-chatterbox-linux.sh
```

That explicit setup command downloads a pinned Chatterbox source revision, PyTorch stack, and model
assets into Project Master's per-user voice-engine directory, then records their hashes and runs a
health check. Its default PyTorch index targets CUDA 12.8; set
`PROJECT_MASTER_TORCH_INDEX` only when intentionally selecting another compatible wheel index.
Normal application startup never runs this installer. Voice cloning also requires a local WAV
reference and an explicit rights attestation in Voice Studio.

## Headless packaged acceptance gate

After the AppImage `master.AppDir` has been staged and verified, run the exact packaged backend
without opening the desktop:

```bash
npm run acceptance:linux
```

This command does not rebuild the candidate, launch the GUI, install ComfyUI, download an Ollama
model, or download Chatterbox assets. It launches only
`master.AppDir/usr/bin/project-master-backend` against a disposable database and workspace. The
installed Chatterbox model/cache files are reused through a temporary copy-on-write clone; the real
desktop profile, database, projects, voice artifacts, and session token are not used.

The gate uses two authenticated backend launches over the same isolated profile:

1. Validate authentication, versions, safe tool diagnostics, Binder indexing/citations,
   Dream schedule controls, supported ComfyUI-offline behavior, and eSpeak/Chatterbox health and
   rendering.
2. Shut down the backend and verify its port closes, releasing the persistent Chatterbox CUDA
   worker.
3. Relaunch with a new session token, reject the old token, verify persisted isolated state, and
   exercise Binder chat, denied/authorized workspace mutation, every physical Ollama model, and a
   proposal-only Dream council.
4. Shut down again, verify the port closes, remove the disposable profile, and write JSON and
   Markdown reports below `release/local/`. If process-tree shutdown or fixture cleanup cannot be
   confirmed, fail the gate and retain the isolated profile at the path recorded in the report for
   diagnosis.

The default reports are:

```text
release/local/Project-Master-0.3.0-linux-x86_64-acceptance.json
release/local/Project-Master-0.3.0-linux-x86_64-acceptance.md
```

The frozen 2026-07-27 candidate passed all 27 checks in 285,963 ms with the GUI closed. It accounted
for 13 installed tags as 12 physical models, passed all 12, and completed the 26B SuperGemma check
in 17,008 ms. Both backend phases stopped cleanly, the disposable profile was removed, and no
Ollama runner remained resident. The canonical JSON report identifies the accepted AppDir backend
as 21,335,944 bytes with SHA-256
`ec4c9098e25bfaa2bde767ed229af4de65cb77330f75f9044079ce19b4437231`.

Every physical model receives one report record. An explicitly non-completion model is an
intentional skip; a conversational timeout, error, empty response, or missing catalog entry fails
the gate. ComfyUI returning HTTP 200 with `ok: false` for a deliberately closed loopback profile is
a supported passing state. This candidate also requires eSpeak and the already-installed
Chatterbox engine to be ready, requires Chatterbox to report CUDA, and checksum-verifies both
rendered WAV files. Before the physical-model loop and after every attempted model, the gate
explicitly unloads only catalog tags it owns and verifies an idle barrier. Loss of Ollama
continuity stops further model/Dream inference instead of turning one daemon failure into a cascade
of misleading model failures.

Run the command with exclusive use of the local Ollama daemon and while no model is resident so the
first Chatterbox phase has uncontested CUDA memory. The gate refuses that phase if `/api/ps` already
lists a loaded model. During later cleanup, noncatalog tags are treated as foreign and left
untouched, but Ollama cannot identify which client loaded a catalog tag; a concurrently loaded
same-tag runner may be unloaded. Per-model, Dream, voice, startup, and overall timeouts can be
adjusted without changing the candidate:

```bash
npm run acceptance:linux -- \
  --model-timeout 750 \
  --dream-timeout 1800 \
  --voice-timeout 900
```

The Chatterbox render uses an eSpeak-generated disposable synthetic WAV rather than a real-person
voice. It is explicitly classified as `synthetic_reference`; it is never represented as the user's
voice, a consented real-person voice, or a licensed voice. The isolated profile disappears with the
temporary database after confirmed shutdown and cleanup.

The desktop currently enables only self-voice and synthetic/generated classifications for
reference profiles. The backend data contract can record explicit-consent or licensed-voice
evidence identifiers, but there is not yet a document upload and verification registry; the
desktop therefore keeps those choices disabled rather than representing an unverified identifier
as evidence.

Ollama thinking metadata is not universally reliable: the installed 26B conversion emitted a
private thinking stream while `/api/show` omitted the capability. The v0.3 client therefore sends
`think: false` defensively for non-GPT-OSS models and requests `low` for GPT-OSS. Automatic
per-model discovery of boolean versus level-based thinking controls remains future work; it must
record whether a mode came from provider metadata or a bounded local probe. If a generation still
spends its full allowance in private thinking, the client makes one hidden, larger visible-answer
retry and never publishes the trace. Ollama's `done_reason` is preserved through streaming so
`length` stops and dropped terminal frames enter the agent's normal bounded continuation path.
Clear untagged task deliberation from converted models is repaired once before it can be stored or
shown; a failed repair produces a neutral user-facing error instead of exposing planning notes.

## Fedora acceptance checklist

Before calling the candidate releasable:

1. Run the complete Python suite and Ruff, frontend tests and production build, and Rust
   formatting, strict Clippy, and native tests shown in **Build and verify the sidecar**.
2. Run `npm run packaging:test`, `npm run packaging:check`, and
   `npm run backend:sidecar:test`.
3. Build the unsigned dogfood candidate with `npm run tauri:build:linux:local` and run
   `npm run packaging:verify`.
4. Run `npm run acceptance:linux` against that exact staged candidate and retain its two reports.
5. Compare the RPM and AppImage against the generated SHA-256 manifest.
6. Verify cold GUI launch, backend recovery, clean shutdown, and relaunch.
7. Verify Ollama model discovery, one Direct response, one Team response, cancellation, and the
   per-chat **Allow project changes** gate.
8. Create and reopen a Project; index and attach its Binder; confirm cited retrieval context.
9. Run a manual Dream and confirm it remains pending in the Proposal Inbox.
10. Render and download an eSpeak artifact. If Chatterbox is part of the release claim, run its
   health and reference-voice smoke test too.
11. If ComfyUI is available, approve and execute one disposable API workflow and verify its local
   artifact checksum. ComfyUI being absent must not prevent startup.
12. Complete the version-matched changelog and GitHub Release notes before any upload.

## Windows

The same sidecar builder and smoke test run on Windows without requiring PowerShell:

```powershell
npm ci
npm run backend:sidecar
npm run backend:sidecar:test
npm run tauri:build:windows
```

Windows produces NSIS and MSI bundles. The existing PowerShell installed-application test remains
available as an additional Windows-specific window-close test, but it is no longer part of the
portable build path.
