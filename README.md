# Project Master — BETA v0.3.0 RC

[Watch Master Mike on YouTube](https://www.youtube.com/@RealMasterMike?sub_confirmation=1) ·
[Creator links](https://linktr.ee/realmastermike) ·
[Support the project](https://streamlabs.com/mastermike/tip) ·
[Creator GitHub](https://github.com/RealMasterMike)

**One command. Many local models. One accountable result.**

Project Master is a local-first multi-AI desktop command center. MASTER coordinates compatible
models already installed in Ollama, keeps work attached to durable projects, retrieves cited local
context, runs bounded tools, and exposes creative integrations without silently granting the model
control of the machine.

> **Release-candidate status:** v0.3.0 is the Linux daily-driver beta candidate. It is ready for
> local acceptance testing but is not a published stable release. Back up important project data,
> keep consequential tool access off until needed, and complete the release gate in
> [`CHANGELOG.md`](CHANGELOG.md) before publishing artifacts.

## What is in v0.3.0

### Multi-AI Ollama Team

- **Team mode** de-duplicates aliases in the local Ollama catalog, then automatically assigns only
  exact publisher-evidenced model identities whose installed manifest digest matches the curated
  registry. It selects a tool-capable lead when available and assigns bounded specialist roles
  based on reported capabilities; other installed models remain manual/unverified rather than
  silently joining the council.
- The council runs specialists sequentially so machines with limited VRAM do not have to keep every
  model active at once. MASTER synthesizes one final response and owns the only tool loop.
- The **Team Strip** and **Run Rail** show model assignments, worker status, tool summaries,
  failures, synthesis, and delivery checkpoints. Specialist drafts and private reasoning are not
  presented as chain-of-thought.
- **Direct mode** remains available when a single model is faster or more appropriate.

### Projects and local RAG

- Projects keep a durable objective, local root, run history, artifacts, and safe metadata events.
- The **Project Binder** indexes supported text and source files below the selected project root,
  skips known secret locations and credential files, versions content by SHA-256, and returns cited
  excerpts.
- Attaching a Binder to chat adds bounded retrieval-augmented generation (RAG) context to Direct or
  Team mode. Retrieval is local lexical SQLite search, not an embedding/vector index. The model
  receives cited excerpts, document versions, and digests rather than an untraceable folder dump.

### Tools with an explicit mutation gate

- Calculator, time, workspace inspection, memory recall, claims, knowledge, Dream, ComfyUI, voice,
  and a bounded workspace terminal are registered through one auditable tool contract.
- Chat is **read-only by default**. Mutating tool schemas are hidden and rejected unless Mike turns
  on **Allow project changes**. The desktop toggle remains on until Mike turns it off, while the
  backend applies the authorization independently to each submitted request so it cannot leak into
  another request context. Starting or reopening a conversation resets the desktop toggle.
- Workspace file tools remain inside the active workspace or selected project root. On Linux,
  terminal execution is project-bound when Bubblewrap is available; without Bubblewrap it falls
  back to a small read-only command allowlist. Terminal network access is independently disabled by
  default.
- The separate **Approval Center** lists and resolves explicit runtime approval records. It does not
  yet intercept every consequential chat tool call automatically, and it never converts product
  proposals or planning decisions into runtime permission.

### Dream Lab

- Run an explicit source through the local model council using a built-in or user-authored Dream
  recipe.
- Schedule recurring Dreams with timezone, quiet-window, catch-up, idle-time, CPU, memory, GPU,
  AC-power, and active-model-job limits. The scheduler runs only while the Project Master backend is
  running. Scheduled Binder sources require an indexed project, explicit per-project consent, and
  a recipe scoped to that project; built-in unscoped recipes remain available for manual runs.
- Every run preserves bounded source snapshots and provenance. Results enter the **Proposal Inbox**
  as speculation; nothing is promoted or acted on until Mike explicitly accepts or rejects it.

### ComfyUI Creator support

- Connect a separately installed local ComfyUI instance, or an explicitly trusted HTTPS remote
  endpoint. ComfyUI itself is not bundled.
- Import a workflow exported with **Save (API Format)**, define typed input bindings, review its
  immutable digest, and approve that exact revision before it can run.
- Queue, monitor, cancel, and reconcile Project Master-owned jobs. Completed image, audio, video,
  and file outputs are retrieved by the backend, size/MIME checked, hashed, and stored atomically
  with workflow and prompt provenance.
- Creator projects add a private Media library, structured idea workspace, and separate
  Text-to-Image, Text-to-Video, Image-to-Image, and Image-to-Video surfaces. Only the four bundled
  publisher-documented defaults are curated automatically; imported workflows remain visibly
  manual/unverified.
- The Creator job ledger presents verified output metadata, safe provenance, authenticated local
  downloads, and inline image, audio, or video previews when the MIME type supports them.
- Loopback is the safe default. Remote profiles require explicit host trust and do not follow
  cross-origin redirects.

### Voice Studio

- The distribution-provided **eSpeak NG** adapter offers a fast multilingual local fallback with
  parameterized pitch, pace, amplitude, pronunciation, and normalized audio output. It builds
  synthetic profiles; it does not train a custom neural voice.
- The optional pinned **Chatterbox** engine adds local reference-voice rendering. Installation and
  model download are an explicit setup step; normal health checks and rendering run offline.
- Voice Studio supports designed voices, user-imported WAV references, distinct synthetic-reference
  classification, rights attestations, script projects, chunked/cancellable jobs, and
  checksum-verified downloadable audio artifacts. A reference WAV is never treated as proof of
  consent or a license.

## Fedora daily-driver quick start

Project Master needs [Ollama](https://ollama.com/download) running locally and at least one
conversational model:

```bash
ollama pull hf.co/TrevorJS/gemma-4-E4B-it-uncensored-GGUF:Q4_K_M
ollama serve
```

For local Creator-image analysis, install the exact physically verified uncensored vision default:

```bash
ollama pull lukey03/qwen3.5-9b-abliterated-vision:latest
```

Automatic chat, vision, Team, and Dream selection require a physically tested tag plus Ollama
manifest digest from the curated registry. Other explicitly selected Ollama models remain
manual/unverified; Project Master does not infer provenance from a model name. Automatic execution
refreshes the installed identity and requires the purpose appropriate to Direct, vision, Team, or
Dream use.

For a source checkout, install the Fedora/Tauri prerequisites listed in
[`docs/LINUX_PACKAGING.md`](docs/LINUX_PACKAGING.md), plus Node.js 20 or newer, Rust stable, and a
supported Python 3.11 through 3.14. Then:

```bash
npm ci
npm run packaging:test
npm run packaging:check
npm run tauri:dev
```

`tauri:dev` builds the host-native Python sidecar and starts the desktop application; a separate
`master serve` terminal is not required. Project Master still opens and exposes diagnostics if
Ollama is temporarily offline.

Optional local voice and terminal support:

```bash
sudo dnf install espeak-ng ffmpeg bubblewrap
bash scripts/setup-chatterbox-linux.sh
```

The Chatterbox command downloads a pinned engine and model set and requires Python 3.11. Skip it if
eSpeak NG is sufficient. ComfyUI is also optional and is installed and started separately.

## Build and test Fedora packages

```bash
npm run tauri:build:linux:local
npm run packaging:verify
```

The local command disables updater artifacts so an unsigned dogfood build does not require release
signing credentials. It produces an RPM and AppImage below `src-tauri/target/release/bundle/`;
verification checks the sidecar and version-matched artifacts and writes local SHA-256 manifests
below `release/local/`. Nothing is uploaded. See
[`docs/LINUX_PACKAGING.md`](docs/LINUX_PACKAGING.md) for signed-release builds, installation,
acceptance testing, troubleshooting, and release checks.

## Windows build path

The Windows NSIS/MSI build path remains in source, but v0.3.0 installers have not yet been validated
against this exact Linux candidate source state. Do not describe Windows v0.3.0 as release-verified
until the roadmap's Windows acceptance item passes. To exercise the build path, install Ollama and
WebView2, then build from a Windows checkout with:

```powershell
npm ci
npm run backend:sidecar
npm run backend:sidecar:test
npm run tauri:build:windows
```

The packaged application manages its Python backend automatically on both platforms. Desktop
configuration, the SQLite database, workspace, artifacts, optional voice engines, and `backend.log`
live in the current user's application-data directory rather than the installation directory.

## Project structure

- `src/App.tsx` — streaming chat state and UI behavior
- `src/components/FeatureWorkspace.tsx` — Projects, Approval Center, Dream, Creator, and Voice dashboards
- `src/App.css` — navy/gold/electric-violet brand tokens and interface styling
- `src/lib/projectMasterApi.ts` — isolated Python API client
- `public/brand/` — owner-supplied primary and heritage identity assets
- `src-tauri/` — native Tauri host, backend lifecycle, packaging, and narrowly scoped permissions
- `ProjectMaster-v0.1.0/` — Python AI engine and local API (legacy directory name)
- `docs/LINUX_PACKAGING.md` — Fedora build, install, and acceptance-test guide
- `docs/UI_CUSTOMIZATION.md` — validated layout architecture and future AI-control boundary
- `CHANGELOG.md` — release history and the mandatory documentation gate for every uploaded build
- `ProjectMaster-v0.1.0/ROADMAP.md` — current implementation scope and release-engineering status

## Change the accent color

Edit the single variable at the top of `src/App.css`:

```css
:root {
  --accent: #f4c928;
}
```

## Created by Master Mike

Project Master is created by **Master Mike**, a content creator, actor, developer, and full-time
funny guy building local-first AI in public.

- [Subscribe on YouTube](https://www.youtube.com/@RealMasterMike?sub_confirmation=1)
- [Follow every platform through Linktree](https://linktr.ee/realmastermike)
- [See more software on GitHub](https://github.com/RealMasterMike)
- [Support development through Streamlabs](https://streamlabs.com/mastermike/tip)

Employment, collaboration, sponsorship, and creator inquiries can be directed through the public
contact options on the [Master Mike Linktree](https://linktr.ee/realmastermike).

## Licensing

Project Master's source code and documentation are available under the [MIT License](LICENSE).
The MASTER AI logos, creator marks, application icons, and related visual identity are excluded
from the MIT License. See the [Project Master Branding Policy](BRANDING.md) for permitted use.
