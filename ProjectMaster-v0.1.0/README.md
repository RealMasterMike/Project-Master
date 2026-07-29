# Project Master Python Engine v0.3.0 Beta RC

[Master Mike on YouTube](https://www.youtube.com/@RealMasterMike?sub_confirmation=1) ·
[Creator links](https://linktr.ee/realmastermike) ·
[Support development](https://streamlabs.com/mastermike/tip)

Project Master is a local-first AI framework designed around **epistemic reliability**: represent
reality as accurately as the available evidence allows, separate facts from claims and inference,
calibrate confidence, and revise conclusions when better evidence appears.

Version 0.3.0 is the packaged engine for the Linux daily-driver beta candidate. It includes:

- Ollama Direct mode and a bounded sequential Team whose automatic roles are limited to exact,
  digest-matched curated model identities;
- a persistent SQLite store for conversations, memory, evidence, projects, runs, and approvals;
- Project Binder indexing and cited local retrieval-augmented generation (RAG) context;
- an explicit per-chat mutation gate over workspace, terminal, memory, evidence, and integration
  tools;
- Dream recipes, resource-aware schedules, provenance snapshots, and a proposal-only inbox;
- validated Creator projects with project-scoped content ideas, media, prompt-driven creation,
  AI image editing/animation, and secondary trimming utilities;
- approved-revision ComfyUI workflows with declared general, image, video, or audio purposes,
  typed project-image inputs, live node/resource compatibility preflight, owned jobs, and verified
  local artifacts;
- a private image, video, and audio library that also catalogs verified project-scoped ComfyUI
  outputs, plus non-destructive H.264/AAC video trimming with durable derivation records;
- project-scoped image attachments for local vision analysis, plus explicitly authorized public
  page reading and optional SearXNG web search;
- Voice Studio contracts and local eSpeak NG and optional pinned Chatterbox adapters;
- adaptive communication profiling, response auditing, cancellation, and streaming;
- a Tauri-managed loopback API with a per-launch session token and a PyInstaller sidecar.

## Design principle

> Do not ask only, “Can I answer this?” Ask, “What confidence does the available evidence justify?”

## Engine quick start

### 1. Prerequisites

- Python 3.11 through 3.14
- Ollama running locally
- At least one Ollama chat model installed

Example model command:

```powershell
ollama pull hf.co/TrevorJS/gemma-4-E4B-it-uncensored-GGUF:Q4_K_M
```

The shipped chat default is the publisher-labeled uncensored Gemma 4 E4B
Q4_K_M build. The model remains configurable; native tool-calling support is
recommended for whichever local model you deliberately select.

Automatic Direct, Team, and Dream selection requires an exact curated tag and the physically
tested Ollama manifest digest. Other installed conversational models may still be selected
deliberately where the interface permits it, but remain manual/unverified and never silently join
an automatic council. Model-less Direct API requests refresh the catalog before resolving the
curated `chat` or `vision` purpose; Team and Dream likewise refresh and enforce their own purpose.

### 2. Install on Linux

```bash
cd ProjectMaster-v0.1.0
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
cp .env.example .env
```

On Windows:

```powershell
cd C:\Master\ProjectMaster-v0.1.0
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

### 3. Configure

Open `.env` and set the model you already have installed:

```env
MASTER_MODEL=hf.co/TrevorJS/gemma-4-E4B-it-uncensored-GGUF:Q4_K_M
MASTER_NUM_CTX=65536
```

The default context is 65536. Reduce it if the selected model cannot sustain that window reliably
on the available RAM and VRAM.

### 4. Verify and run

```text
master doctor
master chat
```

To run the desktop API instead, use `master serve`. It listens on
`http://127.0.0.1:8765`; see [docs/LOCAL_API.md](docs/LOCAL_API.md).

Inside chat:

```text
/help
/profile
/claims
/memories
/audit on
/quit
```

## Creator workspace

Create a project with the **Creator** type to keep its ideas and media separate from general
projects. The Creator workspace is organized around a single selected project:

- **Ideas** turns a topic, audience, platform, tone, goal, constraints, and requested direction
  count into durable Creator Spark proposals. Results remain review-only until you pass on them or
  keep one as a media-brief candidate; nothing is published or placed into production
  automatically.
- **Media** imports supported image, video, and audio files into private content-addressed storage,
  verifies signatures and SHA-256 on read, auto-loads bounded previews as they enter view, and
  shows generated assets from project-scoped ComfyUI jobs after reconciliation.
- **Create** separates text-to-image from text-to-video. **AI Edit** separately exposes
  image-to-image transformation and image-to-video animation using one verified project image.
  Both surfaces are prompt-first, poll owned jobs automatically, preview verified outputs, and
  return results to the project's Media library.
- **Utilities** retains frame-accurate, non-destructive trimming as a secondary media utility. The
  source is never modified: Project Master records the source asset, requested bounds, and
  `mp4-h264-aac-v1` derivation recipe.
- **Workflows** manages immutable approvals and validates required node classes plus audited fixed
  checkpoint, UNet, text-encoder, VAE, and LoRA filenames against the selected live ComfyUI
  profile before a job can run.

Workflow purpose is part of an immutable revision's digest. Changing a workflow from, for example,
`image` to `video` therefore creates a different revision that must be reviewed and approved.
Automatically seeded generation defaults are limited to publisher-documented uncensored or
SFW+NSFW-capable model stacks. User-imported workflows remain possible through explicit immutable
approval, but are manual choices rather than curated defaults.

Automatic Creator-image analysis likewise resolves only the exact installed
`lukey03/qwen3.5-9b-abliterated-vision:latest` tag with the physically tested Ollama manifest
digest. Its publisher documents the abliterated vision build, and that local identity passed both a
minimal Ollama image check and the authenticated Project Master Creator Media chat path at 65,536
context. Other explicitly selected vision models remain manual/unverified rather than being
silently treated as curated.

Project Master coordinates its own Ollama runner with configured ComfyUI profiles on
memory-constrained GPUs. Before interactive Ollama use, it waits for every reachable ComfyUI queue
to become idle and then calls ComfyUI's official `/free` endpoint to unload cached models and free
memory. An active queue remains on the existing local-model busy path, while an offline optional
profile does not block chat. In the other direction, workflow submission unloads only the Ollama
runner tracked as owned by this Project Master process before queueing the ComfyUI prompt.

## Useful commands

```text
master doctor
master chat
master ask "Evaluate this claim and explain your confidence."
master claims
master memories
master audit "This is definitely proven and everyone knows it."
```

## Repository map

```text
constitution/       Project-wide governing principles
config/             Default runtime configuration
docs/               Architecture and subsystem specifications
prompts/             Human-readable prompt drafts
schemas/             JSON schemas for claims, memory, tasks, and responses
src/project_master/  Runnable Python package
tests/               Unit tests
scripts/             Windows-only standalone engine helpers (desktop packaging is at repository root)
```

## Safety and autonomy model

Project Master is designed to be capable without silently taking broad control of the machine.

- Workspace file operations are restricted to `MASTER_WORKSPACE_ROOT` or the selected project's
  local root.
- Mutating tools are omitted from model schemas and rejected by execution unless the current API
  chat request explicitly sets `allow_mutations`.
- External web tools are gated separately: `web_search` and `web_fetch` are omitted and rejected
  unless the current chat request explicitly sets `allow_web_search`. Search contacts only the
  configured SearXNG service; page reading pins validated public DNS addresses, blocks local/private
  targets and redirects to them, and returns bounded text as untrusted reference material.
- Workspace writes also require `MASTER_ALLOW_FILE_WRITES=true`.
- The Linux terminal accepts argument arrays rather than shell strings, enforces resource and output
  bounds, and keeps network access separately disabled by default. It is project-bound when
  Bubblewrap is available; without Bubblewrap it exposes only a read-only command allowlist, whose
  command arguments can still reference host-readable paths.
- Tool results are returned to the model as evidence, not automatically treated as truth.
- Memory stores user-supplied information separately from verified evidence.
- Ordinary conversation remains in the session history. Durable memory writes require an explicit
  mutation-enabled request and remain labeled by source.
- Dream results remain proposals, ComfyUI workflow revisions require approval, and reference voice
  profiles require a rights attestation. Generated audio references use a distinct
  `synthetic_reference` basis; a reference WAV is not accepted as consent or license evidence.

The desktop exposes request-scoped **Allow project changes** and **Allow web access** switches.
Environment settings alone do not grant either chat permission.

```env
MASTER_ALLOW_FILE_WRITES=true
```

## Current limitations

- Ollama and at least one conversational local model remain required for chat and Dream runs.
- Team execution is sequential and bounded; it does not claim simultaneous autonomous agents or
  independent models when only one physical model is installed.
- Local-model tool calling varies by model quality. A tool-capable lead is preferred but cannot be
  manufactured from a model that lacks tool support.
- ComfyUI and Chatterbox are optional, separately installed integrations. They are not silently
  downloaded by the normal application startup path.
- Project Master packages four audited default workflow definitions for text-to-image,
  image-to-image, text-to-video, and image-to-video. It does not package or silently download their
  multi-gigabyte model weights; each installation must provide the documented exact files.
- Project Binder retrieval is lexical SQLite FTS/LIKE search, not an embedding or vector database.
- Adaptive personality remains communication-style adaptation, not psychological inference.
- The response auditor is a deterministic linter, not a second independent verifier model.
- The Tauri beta candidate starts and stops the packaged API automatically. Developers can still run
  `master serve` independently. Standalone `master serve` is unauthenticated unless
  `MASTER_SESSION_TOKEN` is set and accepts an explicit bind host, so do not expose it to an
  untrusted network.

## Development direction

See [ROADMAP.md](ROADMAP.md) for planned work.

## License

MIT. See [LICENSE](LICENSE).

## Creator

Project Master is created by **Master Mike**, a content creator, actor, and developer. Follow the
project and future builds through the [Master Mike Linktree](https://linktr.ee/realmastermike),
[YouTube channel](https://www.youtube.com/@RealMasterMike?sub_confirmation=1), and
[GitHub profile](https://github.com/RealMasterMike). Optional support is available through the
[official Streamlabs page](https://streamlabs.com/mastermike/tip).
