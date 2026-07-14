# Project Master v0.1.0

[Master Mike on YouTube](https://www.youtube.com/@RealMasterMike?sub_confirmation=1) ·
[Creator links](https://linktr.ee/realmastermike) ·
[Support development](https://streamlabs.com/mastermike/tip)

Project Master is a local-first AI framework designed around **epistemic reliability**: the assistant should represent reality as accurately as the available evidence allows, clearly separate facts from claims and inference, calibrate confidence, and revise conclusions when better evidence appears.

Version 0.1.0 is a runnable command-line MVP built for Ollama. It includes:

- an epistemic system prompt and project constitution;
- an Ollama chat provider with tool-call support;
- a persistent SQLite memory and conversation store;
- an evidence ledger for claims and supporting or contradicting evidence;
- adaptive communication-style profiling that mirrors style, not beliefs;
- workspace-scoped file tools, a safe calculator, time, memory, and evidence tools;
- a deterministic response auditor;
- a Windows bootstrap script, tests, schemas, architecture docs, and task files.
- a loopback-only local API for the Tauri client, including streaming responses.

## Design principle

> Do not ask only, “Can I answer this?” Ask, “What confidence does the available evidence justify?”

## Quick start on Windows

### 1. Prerequisites

- Python 3.11 or newer
- Ollama running locally
- At least one Ollama chat model installed

Example model command:

```powershell
ollama pull qwen3:8b
```

The model is configurable. Use any Ollama model that works well on your hardware; tool-calling support is recommended.

### 2. Install

```powershell
cd C:\Master\ProjectMaster-v0.1.0
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
```

Or install manually:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
Copy-Item .env.example .env
```

### 3. Configure

Open `.env` and set the model you already have installed:

```env
MASTER_MODEL=qwen3:8b
MASTER_NUM_CTX=32768
```

The default context is 32768. For an 8 GB GPU, `MASTER_NUM_CTX=8192` is the
recommended starting point; larger contexts can force partial CPU offload or
delay the first response depending on the model and runtime.

### 4. Verify and run

```powershell
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

```powershell
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
tasks/               One task file for every major Project Master subsystem
tests/               Unit tests
scripts/             Windows bootstrap and run scripts
```

## Safety and autonomy model

Project Master is designed to be capable without silently taking broad control of the machine.

- File operations are restricted to `MASTER_WORKSPACE_ROOT`.
- File writes are disabled by default.
- No unrestricted shell tool is included in v0.1.
- Tool results are returned to the model as evidence, not automatically treated as truth.
- Memory stores user-supplied information separately from verified evidence.

Enable workspace writes deliberately:

```env
MASTER_ALLOW_FILE_WRITES=true
```

## Current limitations

- Adaptive personality is lightweight and based on communication signals, not deep psychological inference.
- Web research is not bundled because search providers require different credentials and terms. The tool registry is ready for a search plugin in a later phase.
- The response auditor is a heuristic linter, not a second independent model.
- Local-model tool calling varies by model quality.
- The Tauri alpha client is a separate project and requires this API to run as a second process.

## Project status

See [PROJECT_STATUS.md](PROJECT_STATUS.md), [ROADMAP.md](ROADMAP.md), and [TASKS.md](TASKS.md).

## License

MIT. See [LICENSE](LICENSE).

## Creator

Project Master is created by **Master Mike**, a content creator, actor, and developer. Follow the
project and future builds through the [Master Mike Linktree](https://linktr.ee/realmastermike),
[YouTube channel](https://www.youtube.com/@RealMasterMike?sub_confirmation=1), and
[GitHub profile](https://github.com/RealMasterMike). Optional support is available through the
[official Streamlabs page](https://streamlabs.com/mastermike/tip).
