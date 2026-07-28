# Project Master Python Engine v0.3.0 Beta RC

[Master Mike on YouTube](https://www.youtube.com/@RealMasterMike?sub_confirmation=1) ·
[Creator links](https://linktr.ee/realmastermike) ·
[Support development](https://streamlabs.com/mastermike/tip)

Project Master is a local-first AI framework designed around **epistemic reliability**: represent
reality as accurately as the available evidence allows, separate facts from claims and inference,
calibrate confidence, and revise conclusions when better evidence appears.

Version 0.3.0 is the packaged engine for the Linux daily-driver beta candidate. It includes:

- Ollama Direct mode and a bounded sequential multi-model Team with capability-aware roles;
- a persistent SQLite store for conversations, memory, evidence, projects, runs, and approvals;
- Project Binder indexing and cited local retrieval-augmented generation (RAG) context;
- an explicit per-chat mutation gate over workspace, terminal, memory, evidence, and integration
  tools;
- Dream recipes, resource-aware schedules, provenance snapshots, and a proposal-only inbox;
- approved-revision ComfyUI workflows with typed inputs, owned jobs, and verified local artifacts;
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
ollama pull qwen3:8b
```

The model is configurable. Use any Ollama model that works well on your hardware; tool-calling support is recommended.

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
MASTER_MODEL=qwen3:8b
MASTER_NUM_CTX=8192
```

The default context is 8192. Increase it only after confirming the selected model responds
reliably on the available RAM and VRAM.

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

The desktop exposes the request-scoped mutation switch as **Allow project changes**. The environment
setting alone does not grant a chat permission.

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
