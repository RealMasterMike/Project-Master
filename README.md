# Project Master — ALPHA v0.1.0

[Watch Master Mike on YouTube](https://www.youtube.com/@RealMasterMike?sub_confirmation=1) ·
[Creator links](https://linktr.ee/realmastermike) ·
[Support the project](https://streamlabs.com/mastermike/tip) ·
[Creator GitHub](https://github.com/RealMasterMike)

MASTER is a lightweight, local-first desktop AI agent. The Tauri 2 + React interface now connects
to the Project Master Python engine, which owns Ollama access, tools, memory, evidence,
personality adaptation, response auditing, and conversation persistence.

> **Alpha developer preview:** the desktop interface and Python backend currently run as separate
> local processes. Expect breaking changes. Do not rely on this release for critical work.

## Milestone 1 scope

- One resizable, dark desktop window
- Installed-model discovery through the Python engine
- Persistent Python-backed conversations
- Streaming Project Master responses
- Stop/cancel support
- Clear inline connection and request errors with retry actions
- Branded navy, gold, and electric-violet visual system
- Project Master primary emblem and MM creator mark
- Rendered Markdown, tables, links, lists, blockquotes, and code blocks

The interface remains intentionally small. The engine's existing tools are available to the agent,
but tool activity, memory, evidence, and conversation-management screens are not built yet.

## Windows prerequisites

Install the following before running the native app:

1. Node.js LTS and npm
2. Rust stable through `rustup`
3. Microsoft Visual Studio Build Tools with **Desktop development with C++**
4. Microsoft Edge WebView2 Runtime
5. Ollama with at least one installed model

Official details: <https://v2.tauri.app/start/prerequisites/>

This machine is verified with the MSVC Rust toolchain. On another Windows machine, select it with:

```powershell
rustup toolchain install stable-x86_64-pc-windows-msvc
cd C:\Master
rustup override set stable-x86_64-pc-windows-msvc
```

## Run in development

Start the Python backend in one PowerShell window:

```powershell
cd C:\Master\ProjectMaster-v0.1.0
.\.venv\Scripts\Activate.ps1
master serve
```

Then start the desktop in a second PowerShell window:

```powershell
cd C:\Master
npm install
npm run tauri dev
```

Ollama must be running for the Python backend. Configure the backend `.env` with
`MASTER_MODEL` and `MASTER_NUM_CTX` (default `32768`).

For GPUs with 8 GB of VRAM, start with `MASTER_NUM_CTX=8192`. Increase it only
after confirming that the selected model responds reliably on that machine.

```powershell
# Example model; replace llama3.2 with the Ollama model you prefer.
ollama pull llama3.2
ollama serve
```

## Build the desktop package

```powershell
cd C:\Master
npm run tauri build
```

## Project structure

- `src/App.tsx` — streaming chat state and UI behavior
- `src/App.css` — navy/gold/electric-violet brand tokens and interface styling
- `src/lib/projectMasterApi.ts` — isolated Python API client
- `public/brand/` — owner-supplied primary and heritage identity assets
- `src-tauri/` — native Tauri host and narrowly scoped HTTP permissions
- `ProjectMaster-v0.1.0/` — Python AI engine and local API
- `MILESTONE_1.md` — completed scope, verification, and remaining local setup
- `DESIGN_BRIEF.md` — product source of truth
- `BUILD_HANDOFF.md` — milestone sequence and technical handoff

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
