# Custom AI Agent — Design Brief

This document is the shared design source of truth for every AI collaborating on the project.

> **Decisions locked so far:** local-first do-anything agent, running models through Ollama, "powerful pro-tool" feel, dark theme built on the owner's channel colors, Windows-first (then Linux/Mac), fully customizable layout as a headline feature. First release = a working chat loop that can execute tool calls.
>
> **Open items that need the owner's input are marked `⚠ NEEDS YOUR INPUT`.**

## 1. Identity

- **Working name:** **MASTER** (always styled all-caps). Named after the owner's channel; also matches the `C:\Master` project root.
- **One-sentence purpose:** A local-first, do-anything AI agent that runs models through Ollama and can execute real tool calls to get work done on your machine — no cloud dependency required.
- **Intended users:** Primarily the owner (creator / power user); secondarily technically-comfortable people who want a private, local, heavily customizable agent they own end to end.
- **Three personality words:** Capable, direct, tireless.
- **Desired overall feeling:** A serious pro-tool — fast, information-dense, and always in the user's control. It should feel like a cockpit, not a chatbot.
- **Things it must never feel like:** Toy-like, patronizing, cloud-locked, sluggish, over-animated, or "dumbed down."

## 2. Window and Layout

- **Default window size:** 1440 × 900 (comfortable pro default). Minimum ~1024 × 640.
- **Resizable or fixed:** Fully resizable and maximizable.
- **Main regions or panels:** (1) Left nav rail, (2) center conversation panel, (3) right activity/results panel.
- **Where navigation lives:** Left rail — conversations, projects (later), skills (later), settings.
- **Where conversations live:** Center panel, full height.
- **Where agent activity appears:** Right panel — live tool calls, streaming reasoning/effort, run status.
- **Where files and generated results appear:** Right panel "Results" tab, plus inline result cards in the conversation.
- **Panels that can collapse, resize, float, or detach:** **All of them.** This is a headline feature — every panel can collapse, resize, pop out into its own window, or be docked to a different edge. Ships with the owner's preferred default arrangement; users can rearrange freely and save layouts.
- **Should multiple conversations or projects be visible simultaneously?** Yes — optional split view / tabbed conversations for power users.
- **Rough layout description or sketch:** Three-column shell. Slim left rail (collapsible to icons). Center conversation takes the majority of width. Right panel shows agent internals + results and can be hidden when not needed. Everything is drag-to-rearrange with saveable layout presets.

## 3. Navigation

- **Primary destinations:** Conversations, Projects (later), Skills (later), Settings.
- **What should always remain visible:** The active conversation and the composer. The left rail can collapse to icons but never fully disappears.
- **What belongs in menus or secondary screens:** Settings, model management (Ollama pull/list), layout presets, theme editor.
- **How users create a new conversation:** "New chat" button at top of left rail + keyboard shortcut (Ctrl+N).
- **How users switch projects:** Project switcher at top of left rail (first release: single default workspace).
- **How users access skills:** Skills entry in left rail (post-MVP).
- **Keyboard-first, mouse-first, or balanced:** Balanced, but with a **strong keyboard layer** (command palette, shortcuts for new chat, stop, switch panel focus) to match the pro-tool feel.

## 4. Conversation Experience

- **Message appearance:** Clean, dense, monospace-friendly. Clear visual separation between user and agent turns. Markdown, code blocks with syntax highlighting, copy buttons.
- **Composer location and behavior:** Fixed at bottom of center panel. Multi-line, auto-grow, send on Enter (Shift+Enter for newline). Shows selected model inline.
- **Attachments:** Drag-and-drop files into the composer or conversation; paperclip button as fallback.
- **Voice input:** Deferred (post-MVP). Reserve a mic button slot in the composer.
- **Model selector placement:** In/near the composer — a dropdown listing installed Ollama models, switchable per conversation.
- **Reasoning or effort controls:** Toggle in the right panel to show/hide the model's reasoning/tool-call trace.
- **Stop, pause, redirect, and retry controls:** Prominent **Stop** button while generating; **Retry** on any agent turn; ability to edit-and-resend a user message to redirect.
- **How tool activity should be displayed:** Live in the right panel as expandable step cards (tool name, inputs, output/result, status). Collapsed by default, expandable for detail.
- **How errors should appear:** Inline, non-blocking error cards with a plain-language reason and a retry action. Ollama connection errors get a dedicated, clear message (e.g., "Ollama not reachable at localhost:11434").
- **How completed files and results should appear:** As result cards inline in the conversation and collected in the right-panel Results tab.
- **Information that should remain hidden unless expanded:** Raw tool inputs/outputs, full reasoning traces, token counts, timing — all available on expand, hidden by default.

## 5. Agent Behavior

- **How autonomous should it be?** Configurable, with high capability. **Owner wants the agent to have broad access to the system** (full filesystem + shell, not sandboxed to a single folder). Autonomy level is a user setting.
- **Two separate axes (don't conflate them):** (1) *What the agent is allowed to touch* — owner wants this wide open. (2) *Whether an action runs automatically vs. after a confirmation click* — this is independent of the model being uncensored. Recommendation: keep capabilities broad but leave a confirmation gate on destructive/irreversible operations by default, because an uncensored model won't self-restrain and *will* occasionally run a wrong command — the gate protects the owner's own machine, not the content. The owner can turn the gate off if they want a fully hands-off mode.
- **When must it ask permission?** Owner's call. Default suggestion: confirm before irreversible actions (mass delete, overwrite, `rm -rf`, formatting, etc.); everything else runs freely.
- **Actions it may perform without permission:** Reading files, searching, running safe read-only tools, generating content.
- **Actions it must never perform automatically:** Deleting data, overwriting files without confirmation, running destructive shell commands, network actions the user hasn't enabled.
- **Should it create plans before acting?** Optional "plan mode" — for multi-step tasks it can show a plan first, then execute on approval.
- **Should it remember information across conversations?** Deferred (post-MVP). First release is stateless per conversation.
- **Should users inspect, edit, and delete memory?** Yes — when memory ships, it must be fully inspectable and editable.
- **Should it support subagents or parallel tasks?** Deferred (post-MVP). Architecture should not preclude it.
- **How should long-running work be shown?** Live status in the right panel with a progress/step indicator and a Stop control.

### YOLO Mode (hands-off auto-execution)

An explicit **YOLO mode** that removes all confirmation gates — the agent executes every tool call (filesystem, shell, everything) automatically with no prompts. This is the "fully hands-off" option referenced above, exposed as a first-class toggle.

- **Default state:** OFF. YOLO is always an explicit opt-in, never the out-of-box default.
- **Activation:** A clear toggle (and/or a per-message "run this in YOLO" option). Session-scoped by default — resets to gated on app restart so it's never left on by accident. A setting can make it persistent for users who want that.
- **On-state indicator:** Unmistakable persistent visual signal while active (e.g., accent-colored border/banner around the window) so the user always knows gates are off.
- **Kill switch:** A global, always-available **instant stop** (big button + `Esc` / global hotkey) that halts execution immediately mid-action. This is non-negotiable — it's what makes hands-off mode safe to use.
- **Activity log:** Every action taken in YOLO mode is written to a live, reviewable log (command, target, result, timestamp). Nothing happens silently; the user can always see exactly what was done and in what order.
- **Optional guardrails even in YOLO:** Owner's choice — e.g., an optional "hard stop" list of never-auto operations (disk format, deleting outside the project root) that still prompt even in YOLO. Off by default if the owner truly wants zero friction.
- **Scope:** Inherits the broad full-system access from the autonomy settings above; YOLO is about *removing the confirmation step*, not expanding what the agent can reach.

## 6. Skills Library

*(Post-MVP — captured here so the architecture leaves room.)*

- **Where the Skills area belongs:** Dedicated section from the left rail.
- **Skill presentation:** Cards in a grid, with a list-view toggle.
- **Information shown for each skill:** Name, description, category/tags, enabled state, source.
- **Enable or disable behavior:** Per-skill toggle.
- **Categories, tags, favorites, and search:** All supported.
- **Import and export:** Yes.
- **Installation from local folders:** Yes.
- **Installation from URLs or repositories:** Yes.
- **Should skills follow the open `SKILL.md` format?** Yes — commit to the open `SKILL.md` format for portability.
- **Should skills be editable inside the app?** Yes — built-in editor.

## 7. Skill Creator

*(Post-MVP.)*

- **Blank editor, guided wizard, AI-assisted chat, or combination:** Combination — AI-assisted chat as the primary path, with a blank editor for power users.
- **Required fields:** Name, description, instructions.
- **Create skills from written instructions:** Yes.
- **Create skills from existing conversations:** Yes ("turn this conversation into a skill").
- **Create skills from local documents or folders:** Yes.
- **Create skills from websites:** Yes (later).
- **Create skills from demonstrated workflows:** Stretch goal.
- **Preview and testing experience:** In-app test runner before saving.
- **Version history:** Yes.
- **Approval required before saving:** Yes — user reviews before commit.
- **Desired skill-editor layout:** Split view — editor on the left, live preview/test on the right.

## 8. Projects, Files, and Results

- **Does each conversation belong to a project?** Post-MVP: yes. First release: a single default working folder.
- **Working-folder behavior:** Each project maps to a folder on disk; the agent operates within it.
- **File browser:** Yes — in the right panel or a dedicated view.
- **File editing:** Basic in-app editing for text/code (later); MVP can open externally.
- **Side-by-side previews:** Yes for generated results.
- **Supported preview types:** Text, code, Markdown, images; more later.
- **Generated artifact gallery:** Results tab collecting everything the agent produced.
- **Version history or checkpoints:** Deferred; desirable later.
- **Git integration:** Deferred (post-MVP).
- **Drag-and-drop behavior:** Drag files in to attach; drag results out to save.

## 9. Models and Connections

- **Initial model providers:** **Ollama (local) — this is the MVP focus.**
- **Local models:** Yes, primary. Manage (list/pull/remove) Ollama models from within the app. **Owner is running an uncensored local model** and wants no content filtering imposed by the app layer — the app should pass prompts/responses through unmodified and not add its own refusals or guardrails on top of the model.
- **Bring-your-own API keys:** Deferred — add cloud providers (Anthropic/OpenAI/etc.) after local is solid.
- **Per-conversation model selection:** Yes.
- **Automatic model routing:** Deferred.
- **MCP or tool-server support:** Yes — **tool calling is the core MVP capability.** Design the tool layer to be MCP-compatible so external tool servers can plug in.
- **Web search or browser access:** Deferred / optional tool.
- **External applications or services to connect:** Filesystem and shell tools first; others via the tool/MCP layer later.

## 10. Visual System

- **Light, dark, or both:** **Dark only** for first release (light theme later).
- **Primary colors:** ⚠ NEEDS YOUR INPUT — built around **your channel colors**. Please provide the exact hex values (primary + accent). Structure: deep neutral dark base (near-black / charcoal) + your channel color as the primary brand accent.
- **Accent colors:** ⚠ NEEDS YOUR INPUT — secondary accent from your channel palette (for highlights, active states, links).
- **Background treatment:** Flat dark surfaces with subtle elevation layers (no heavy gradients). Pro-tool, low-distraction.
- **Typography:** Clean sans-serif for UI (e.g., Inter); monospace for code and tool output (e.g., JetBrains Mono).
- **Corner style:** Small, consistent radius (~6–8px) — modern but not bubbly.
- **Border style:** Thin, low-contrast dividers to separate dense panels.
- **Shadow style:** Minimal — used only to lift floating/detached panels.
- **Spacing density:** **Dense** — this is a pro-tool; prioritize information density over whitespace.
- **Icon style:** Consistent line-icon set (e.g., Lucide/Phosphor).
- **Animation style:** Fast and functional — quick transitions, no decorative motion. Nothing should slow the user down.
- **References you like:** ⚠ Optional — add apps whose look/feel you admire (e.g., VS Code, Linear, Raycast).
- **References you dislike:** ⚠ Optional.
- **Accessibility requirements:** Sufficient contrast on the dark theme, full keyboard navigation, respect reduced-motion settings.

## 11. Important Screens

1. **Main Conversation (shell)**
   - **Purpose:** The core workspace — chat with the agent and watch it work.
   - **Visible elements:** Left rail, conversation, composer, right activity/results panel.
   - **Primary action:** Send a message.
   - **Secondary actions:** Switch model, stop, retry, toggle panels, attach files.
   - **Empty state:** "Start a conversation" prompt + model picker + a note if Ollama isn't running.
   - **Loading state:** Streaming response with live tool-call cards.
   - **Error state:** Inline error card (esp. Ollama connection failures) with retry.

2. **Model Manager**
   - **Purpose:** See, pull, and remove Ollama models.
   - **Visible elements:** Installed model list, pull-new field, status/size.
   - **Primary action:** Pull a model.
   - **Secondary actions:** Set default, remove.
   - **Empty state:** "No models installed — pull one to get started."
   - **Loading state:** Download progress bar.
   - **Error state:** Pull/connection failure message.

3. **Settings**
   - **Purpose:** Configure the app.
   - **Visible elements:** Ollama endpoint, permissions/autonomy level, theme editor, layout presets, shortcuts.
   - **Primary action:** Save settings.
   - **Secondary actions:** Reset to defaults.
   - **Empty state:** N/A (defaults shown).
   - **Loading state:** N/A.
   - **Error state:** Invalid endpoint warning.

4. **Layout / Theme Customizer**
   - **Purpose:** Rearrange panels and set colors (headline feature).
   - **Visible elements:** Drag-and-drop panel map, saved presets, color pickers seeded from channel palette.
   - **Primary action:** Save layout/theme preset.
   - **Secondary actions:** Reset, duplicate preset.
   - **Empty state:** Default layout loaded.
   - **Loading state:** N/A.
   - **Error state:** N/A.

5. ⚠ *Add any additional screens you're imagining (e.g., Skills, Projects, Skill Creator — currently scoped post-MVP).*

## 12. First Release

- **Three essential capabilities:**
  1. Chat with a local model served by **Ollama** (per-conversation model selection).
  2. **Tool calling** that actually executes inside the app (filesystem/shell tools, MCP-compatible layer) with visible tool-activity cards.
  3. **Customizable dark UI** — the three-panel shell with collapsible/rearrangeable panels, themed to the channel colors.
- **Features that can wait:** Skills library, Skill Creator, projects, cross-conversation memory, subagents, cloud model providers, voice, git integration.
- **Features explicitly excluded (v1):** Cloud-only dependencies, telemetry, anything that breaks local-first/offline use.
- **What would make the first prototype feel successful?** You can open the app, pick an Ollama model, ask it to do a multi-step task, watch it call tools and complete the work in a fast, dense, dark UI that already looks like your channel.

## 13. Non-Negotiables

- **Must include:** Local Ollama support; working in-app tool calls; fully customizable, dark, channel-branded UI.
- **Must avoid:** Cloud lock-in, forced sign-in, sluggishness, toy-like or patronizing UX.
- **Privacy expectations:** Local-first. No data leaves the machine unless the user explicitly enables a network tool. No telemetry by default.
- **Offline expectations:** Fully usable offline with local models.
- **Performance expectations:** Snappy UI, fast streaming, minimal overhead — it must never feel slower than talking to Ollama directly.
- **Other rules:** Commit to open formats (`SKILL.md`) where relevant; keep the tool layer MCP-compatible; keep implementation files and documentation inside `C:\Master`.

## 14. Intro / Onboarding Flow

The first-run experience. Runs **as a chat** — the agent interviews the user conversationally rather than showing a traditional form wizard. This doubles as a live demo of the core chat loop (the MVP itself). Onboarding is **skippable** and **re-runnable** later from Settings; every choice made here is editable afterward.

> **Implementation note:** the setup portion (welcome + model connection) should be a *scripted* branching chat — predefined messages and choice chips — because no model is connected yet to drive a live conversation. Once a model is connected, the use-case portion can either stay scripted or be handed to the live model. Scripted is recommended for v1 reliability.

### Flow phases

**Phase 0 — Welcome.** Short greeting that sets the tone (pro-tool, local-first, private). One line on what the app is and that setup takes ~1 minute.

**Phase 1 — Model connection.** *(v1-essential)*
- Auto-detect Ollama at `http://localhost:11434`.
- **Ollama running + models installed:** list them, let the user pick a default. Flag which installed models support native tool calling (since tool use is the core capability).
- **Ollama running, no models:** prompt to pull one, suggest a tool-capable default, show download progress.
- **Ollama not detected:** clear instructions to install/start it, plus a Retry button. Never a dead end.
- **Cloud providers:** shown as a visible but disabled **"Coming soon"** section (Anthropic / OpenAI / etc.), so users know it's on the roadmap without it being selectable in v1.

**Phase 2 — Use-case interview.** *(v1-desirable)* Conversational questions to learn intent:
- Primary question: "What are you mainly going to use this for?" → maps to the four presets below (multi-select; user can pick a primary).
- Light branching follow-ups per selection (e.g., Content Creation → which platforms; Coding → which languages/stack). Kept short — a few taps, not an inquisition.
- Answers tune four things: the default **workspace layout**, the **default tools enabled**, the **system-prompt/persona lean**, and the **suggested model**.

**Phase 3 — Workspace recommendation.** *(v1-desirable)* Based on the interview, recommend one of the four presets (or a sensible blend), show a **live preview of the layout**, and let the user Accept or Customize. This is where the "custom-built workspace designs for specific scenarios" value prop lands, and it hooks directly into the customizable-layout system from Section 2.

**Phase 4 — Finish.** Recap the choices, reassure that everything is changeable in Settings, then drop the user into their configured main conversation.

### Workspace Presets (ship all four)

Each preset is a bundle of: **layout arrangement + default tools + persona lean + suggested model.**

- **General / Do-anything** — the balanced default 3-panel shell; all common tools on; neutral, capable assistant persona; any solid general model. The safe fallback if the user is unsure.
- **Content Creation** — layout emphasizes the results/media panel; tools geared to writing, scripting, and media handling; persona tuned for ideation, hooks, and scripting (fits the owner's channel work); model favored for creative fluency.
- **Research & Writing** — document-forward layout (wider conversation + document/preview pane); web-search + file/reading tools on; citation-minded, precise persona; model favored for long-context and accuracy.
- **Coding / Dev** — IDE-style multi-panel (file browser + shell/terminal + code preview prominent); filesystem and shell tools front and center; concise, technical persona; tool-capable model strongly recommended and surfaced in the model step.

### Screen additions (for Section 11)

- **Onboarding / First-run chat** — Purpose: configure model + intent and land in a matching workspace. Primary action: continue through phases. Secondary: skip, back. Empty/entry state: welcome message. Loading state: model detection + pull progress. Error state: Ollama-not-found with retry.

## Collaboration Notes

- Treat this file as the authoritative product and design brief.
- Preserve decisions already written here unless the owner explicitly changes them.
- Record unresolved questions instead of silently inventing major product decisions.
- Keep implementation files and documentation inside `C:\Master`.

### ⚠ Open questions for the owner
1. **Channel colors** — provide exact hex for primary + accent so the theme can be locked.
2. **Design references** (optional) — any apps whose look/feel you want to match or avoid.
3. **Additional screens** (optional) — anything beyond those already listed you picture.
