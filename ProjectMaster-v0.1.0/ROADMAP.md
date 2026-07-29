# Roadmap

## v0.3.0 beta scope — implemented

1. Local Ollama discovery with one visible role per physical conversational model
2. Bounded sequential Team councils with persistent roles, activity, cancellation, and synthesis
3. Explicit per-chat mutation authorization and constrained workspace/terminal tools
4. Project workspaces with an immutable, cited Binder index
5. Manual and scheduled Dream councils that create review-only proposal inbox items
6. Versioned ComfyUI profiles, approved workflow revisions, owned jobs, and verified artifacts
7. Voice Studio with eSpeak NG designed voices and optional consent-gated Chatterbox references
8. Linux RPM/AppImage packaging with an authenticated per-launch local sidecar

This is the feature-complete boundary for the v0.3.0 Linux daily-driver beta. It does not declare
the entire long-term roadmap complete.

## Phase 1 — Foundation

1. Constitution and epistemic behavior
2. Persistent memory and evidence ledger
3. Ollama provider and tool-call loop
4. CLI reference implementation

**Status:** v0.1 foundation shipped.

## Phase 2 — Research and planning

1. Add a search-provider plugin interface
2. Extend Binder ingestion into a provenance graph
3. Add claim decomposition and contradiction detection
4. Expand the persistent project/run/task model into an editable planner
5. Produce research reports with confidence calibration

## Phase 3 — Interaction

1. Add voice input, accessibility, and dictation correction
2. Expand designed neural voices beyond the eSpeak fallback
3. Add a dedicated runtime Settings workspace
4. Extend declarative personalization while preserving validation and undo

**Status:** Tauri desktop, streaming chat, creator surfaces, layout controls, and local text-to-speech
are included in v0.3.0.

## Phase 4 — Controlled autonomy

1. Generate actionable Approval Center requests before consequential operations
2. Add preview/dry-run receipts and reversible checkpoints
3. Isolate mutating workers before enabling parallel execution
4. Add stronger rollback and end-to-end completion verification

**Status:** v0.3.0 includes explicit mutation gates, constrained tools, durable runs/jobs, Dream
resource governance, cancellation, recovery, and proposal-only background work. Unattended parallel
mutation remains out of scope.

## Phase 4a — Resource awareness and model self-knowledge

Planned 2026-07-29. Deterministic work that needs no GPU to build, and that exists largely to stop
wasting a single 8 GB GPU.

1. Generation queue with explicit GPU leases and a **run when the GPU is free** submit option
2. VRAM fit preflight that distinguishes "never fits this hardware" from "does not fit right now"
3. Storage and model manager exposing reverse references from curated workflows to model files
4. Run ledger recording wall time, queue wait, VRAM high-water mark, and load/unload events
5. Selectable thinking modes per purpose, replacing the currently hardcoded minimum-cost policy
6. Per-model thinking capability matrix — none, boolean, levelled, forced, or unknown
7. Model Interview: a free metadata pass plus an opt-in, lease-queued live capability pass

**Status:** planned only. Items 1 to 4 came from failures and blockers recorded in the v0.4.0
acceptance sweep. Interview evidence informs recommendations but never promotes a model to an
automatic curated default; that still requires publisher documentation.

## Phase 4b — Everyday surfaces

Planned 2026-07-29. Small deterministic surfaces over data already stored. No GPU, no network, no
new runtime dependency.

1. Global search across projects, ideas, media, runs, chat, and approvals via SQLite full-text search
2. Conversation, run, and Binder export to Markdown with provenance and secret redaction
3. Project-scoped, revision-pinned prompt recipes
4. Command palette reusing the existing workspace table and streaming lock

**Status:** planned only.

## Phase 5 — Self-evaluation

1. Independent verifier model
2. Citation entailment checks
3. Calibration benchmarks
4. Hallucination regression suite
5. Model and prompt A/B evaluation

## Phase 6 — Plugin ecosystem

1. Stable plugin manifest
2. Sandboxed tools
3. Capability permissions
4. Plugin registry
5. Community contribution process

## Release engineering

1. Complete Mike's installed Linux acceptance pass
2. Validate Windows installers against the same v0.3.0 source state
3. Provision and sign the beta updater channel
4. Publish only after the local candidate, hashes, release notes, and rollback path are reviewed
