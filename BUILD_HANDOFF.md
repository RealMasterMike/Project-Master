# MASTER — Build Hand-off

Companion to `DESIGN_BRIEF.md`. The brief is the **product** doc (what/why). This is the **technical** doc (how) — written to hand directly to a coding agent so it can start building with minimal back-and-forth.

Project root: `C:\Master`.

---

## 1. What you're building (context for the coding agent)

MASTER is a **local-first desktop AI agent**. It runs language models locally through **Ollama** and can execute **tool calls** (filesystem, shell) to do real work on the user's machine. Dark, dense, fast "pro-tool" UI. Windows first, then Linux/Mac. Fully local and private — no cloud dependency, no telemetry.

The first release is deliberately small: **a working chat that talks to a local Ollama model and can execute tool calls.** Everything else (skills, projects, memory, onboarding presets) comes later.

Full product detail lives in `DESIGN_BRIEF.md` — read it for UX, layout, and behavior specifics.

---

## 2. Tech stack — CONFIRMED: Tauri

**Stack: Tauri** (Rust core + web frontend). Decision locked by owner.

- **Why Tauri:** small binary, low memory (satisfies the "never slower than Ollama" performance rule); the Rust core is ideal and safe for the parts that matter most here — spawning shell commands, filesystem access, and running the tool-execution layer; genuinely cross-platform (Windows → Linux/Mac) from one codebase.
- **Alternative: Electron** (Node + Chromium). Bigger ecosystem and all-JS, faster to prototype, but heavier on memory — cuts against the dense/fast goal. Pick this only if the team is much more fluent in Node than Rust.
- **Frontend:** React (largest component ecosystem) or Svelte (lighter, fits the perf goal). Either is fine; React suggested for available UI building blocks.

> Note: the app logic is the same regardless of host; only the backend differs. Tauri is locked.

---

## 3. Architecture (major pieces)

- **UI shell** — the 3-panel layout (nav / conversation / activity+results). Panels collapsible & rearrangeable (later milestone).
- **Ollama client** — talks to `http://localhost:11434`:
  - `POST /api/chat` — chat with streaming; supports a `tools` field for native tool calling.
  - `GET /api/tags` — list installed models.
  - `POST /api/pull` — download a model (stream progress).
  - health check — detect whether Ollama is running.
- **Tool-calling engine** — the core loop: send message + tool schema → model returns `tool_calls` → dispatch to tool implementations → feed results back → model continues → repeat until done. Render each step as an activity card.
- **Tool implementations** (in the Rust core for Tauri): read file, list dir, write file, run shell command. Start read-only, add write/shell behind the permission layer.
- **Permission / YOLO layer** — confirmation gate on destructive/irreversible actions by default; YOLO mode removes gates (with kill switch, activity log, on-indicator). See brief §5.
- **Config store** — a JSON file in `C:\Master` (e.g. `master.config.json`) holding: chosen model, workspace preset, saved layout, YOLO setting. Human-readable so other tools can inspect it.

---

## 4. Build sequence (milestones)

Build in this order. Each milestone is testable on its own before moving on.

1. **M1 — Skeleton.** One window, dark UI, input box + send, connects to Ollama `/api/chat`, streams the reply back. No tools. *(This is the first prompt in §5.)*
2. **M2 — Model management.** Detect Ollama, list models (`/api/tags`), pick a default, pull new (`/api/pull`) with progress. Handle "Ollama not running."
3. **M3 — Tool-calling loop.** Define a tool schema; implement 1–2 safe read-only tools (read file, list dir); wire the model → tool_call → execute → result → continue loop; render tool activity cards.
4. **M4 — Write/shell tools + permission gate.** Add file-write and shell-exec tools behind a confirmation gate for destructive/irreversible actions.
5. **M5 — YOLO mode.** Toggle (off by default, resets on restart), instant kill switch, live activity log, loud on-state indicator.
6. **M6 — Onboarding chat + workspace presets.** The scripted first-run chat (model connect → use-case interview → recommended preset). Four presets: General, Content Creation, Research & Writing, Coding/Dev.
7. **M7 — Layout customization + theming.** Drag/collapse/detach panels, saved layouts, channel-color theme.

---

## 5. First prompt for the coding agent (Milestone 1 — copy/paste)

> **Build the skeleton of a desktop app called MASTER using Tauri with a React frontend.**
>
> Requirements for this first version only:
> - A single resizable window, dark theme (near-black background, light text, one accent color — leave the accent as a single CSS variable I can change later).
> - A vertical chat view: a scrollable message list on top, and a fixed message composer at the bottom (multi-line text input that grows, plus a Send button; Enter sends, Shift+Enter makes a newline).
> - On send: call the local Ollama API at `http://localhost:11434/api/chat` with streaming enabled, and render the assistant's reply token-by-token as it streams in. Show user messages and assistant messages as distinct bubbles/rows.
> - A simple model dropdown at the top populated from `GET http://localhost:11434/api/tags` (list of installed models). Use the selected model in the chat request.
> - Error handling: if Ollama isn't reachable, show a clear inline message ("Ollama not reachable at localhost:11434 — is it running?") with a Retry button. Never crash or hang silently.
> - A visible Stop button while a response is streaming that cancels the in-flight request.
>
> **Do NOT build yet:** tool calling, file/shell access, YOLO mode, onboarding, workspace presets, or any persistence beyond the current session. Keep it to a clean, well-structured skeleton I can build on. Organize the code so the Ollama client is its own module.
>
> Target Windows first, but don't do anything that would block a later Linux/Mac build.

---

## 6. Notes for the owner

- Hand over **both** files (`DESIGN_BRIEF.md` for full context + this `BUILD_HANDOFF.md` for the plan). The coding agent can read the brief when it needs UX detail.
- After M1 works, the next prompt is M3's tool-calling loop — that's the real heart of MASTER and worth designing carefully before writing it. Ask for that spec when you're ready.
- Open product question still outstanding: **channel colors** (theme). M1 leaves the accent as a single variable so it's a one-line change once you pick them.
