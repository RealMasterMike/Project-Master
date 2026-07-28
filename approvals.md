# Project Master Approvals

**Decision owner:** Mike
**Last reviewed:** 2026-07-27

## Purpose

This is Mike's product-decision queue for Project Master. Raw possibilities and experiments belong
in [`ideas.md`](ideas.md). An item moves here when there is a concrete choice, recommendation, and
tradeoff.

This file is not the future in-app Approval Center for commands and file changes. Runtime
permissions belong in the product; this document records product and architecture decisions.

## Status key

- `PROPOSED` — ready for Mike's decision
- `APPROVED` — direction is locked and may be implemented
- `REVISE` — promising, but the proposal needs changes
- `DEFERRED` — valid decision intentionally postponed
- `REJECTED` — do not pursue unless reopened with new evidence
- `SUPERSEDED` — replaced by a newer decision ID

## Decision rules

1. Use one atomic decision per `PM-D###` ID.
2. Silence is never approval. Only Mike, or someone he explicitly delegates, marks a proposal
   `APPROVED`.
3. Record an owner note and decision date when a status changes.
4. Keep old decisions. If direction changes materially, mark the old decision `SUPERSEDED` and link
   its replacement.
5. Reflect approved direction in the current roadmap, changelog, or another authoritative document.
   `DESIGN_BRIEF.md` preserves the original MVP brief; this file records product decisions, and only
   entries Mike formally marks `APPROVED` are binding.
6. Approval applies only to the stated scope. It is not blanket permission for unrelated changes.

## Locked baseline — not awaiting approval

The original [`DESIGN_BRIEF.md`](DESIGN_BRIEF.md) remains the historical source for local-first
Ollama operation, real tool execution, the dense dark cockpit feel, customizable layouts, privacy
and no telemetry by default, MCP-compatible tools, open `SKILL.md` skills, and the proposed
explicit opt-in YOLO mode. Its original Windows-first milestone order and single-agent capability
snapshot do not describe the current v0.3 release state; use the roadmap and changelog for that.

Mike has also explicitly locked this direction:

- **Project Master is a multi-AI project.**
- The multi-AI team must be obvious in the product identity and interface.
- A single-agent implementation is a temporary alpha stage, not the intended product identity.

Reversing a locked baseline decision requires a new decision ID.

## Approval queue

### PM-D001 — Lock the multi-AI product identity

- **Status:** `APPROVED`
- **Priority:** P0
- **Decision:** Define Project Master as a local-first multi-AI command center where MASTER
  coordinates specialist AIs to plan, research, create, build, operate, and independently verify
  work. Make the team visible in onboarding, the main workspace, documentation, and release
  messaging.
- **Why:** This is the product's core direction, not a later feature.
- **Implementation note:** Public copy must distinguish the intended product from the capabilities
  actually shipped in the current alpha.
- **Owner note:** Mike: “This is a multi AI project. Make sure it's obvious.”
- **Decision date:** 2026-07-27

### PM-D002 — Set the canonical product and agent names

- **Status:** `PROPOSED`
- **Priority:** P0
- **Decision:** Use **Project Master** for the desktop platform, **MASTER** for the lead/orchestrator
  AI inside it, and **MASTER AI** only for the protected visual identity and marketing lockup.
- **Recommendation:** Approve.
- **Why now:** The repository currently uses all three labels. A hierarchy will keep UI copy,
  releases, roles, plugins, and documentation coherent.
- **Tradeoff:** Three related names require one short branding explanation, but that is clearer than
  using them interchangeably.
- **Owner notes:** _Mike: add note here._
- **Decision date:** —

### PM-D003 — Approve the initial AI team roster

- **Status:** `PROPOSED`
- **Priority:** P0
- **Decision:** Define six reusable role templates: **MASTER / Lead**, **Researcher**, **Builder**,
  **Creator**, **Operator**, and **Verifier**. The first implementation activates only MASTER,
  Builder, and Verifier; the rest become functional slices as their tools and evidence contracts
  mature.
- **Recommendation:** Approve.
- **Why now:** The roster makes multi-AI concrete while keeping the first build narrow.
- **Tradeoff:** A visible roster may imply simultaneous model loads. The UI must distinguish
  available roles from currently running inference.
- **Owner notes:** _Mike: rename, add, or remove roles here._
- **Decision date:** —

### PM-D004 — Use lead-owned hub-and-spoke orchestration first

- **Status:** `PROPOSED`
- **Priority:** P0
- **Decision:** Mike speaks primarily to MASTER. MASTER records the objective, constraints,
  acceptance criteria, and task graph; delegates bounded work; receives structured handoffs; and
  owns final integration. Workers cannot recursively spawn agents in the first implementation.
  Default concurrency is at most two workers with hardware-aware queuing.
- **Recommendation:** Approve.
- **Why now:** Hub-and-spoke orchestration is easier to inspect, cancel, recover, and explain than an
  unrestricted swarm.
- **Tradeoff:** MASTER can become a bottleneck and two-worker concurrency is conservative. Both can
  be relaxed later with evidence.
- **Owner notes:** _Mike: add note here._
- **Decision date:** —

### PM-D005 — Build Verified Team Run as the first multi-AI slice

- **Status:** `PROPOSED`
- **Priority:** P0
- **Decision:** Build one persistent sequential workflow:
  `MASTER → Builder → Verifier → MASTER`. Mike approves the task graph; Builder produces a bounded
  artifact; Verifier checks it against the original criteria; MASTER delivers a completion receipt.
- **Recommendation:** Approve.
- **Why now:** It creates a real multi-AI experience without prematurely enabling parallel agents to
  mutate shared state.
- **Tradeoff:** Sequential execution is less visually dramatic, but it proves the contracts needed
  for safe parallelism.
- **Owner notes:** _Mike: add note here._
- **Decision date:** —

### PM-D006 — Show team activity, not raw chain-of-thought

- **Status:** `PROPOSED`
- **Priority:** P0
- **Decision:** The activity view records agent identity, task delegation, handoff, user-facing plan,
  tool request, affected target, result, verification, and timestamp. It does not promise or label
  hidden model chain-of-thought as a product feature.
- **Recommendation:** Approve and revise the “streaming reasoning” wording in `DESIGN_BRIEF.md`.
- **Why now:** This is provider-independent, auditable, and more useful for debugging and trust.
- **Tradeoff:** It is less theatrical than a reasoning stream, but it is more honest.
- **Owner notes:** _Mike: add note here._
- **Decision date:** —

### PM-D007 — Use structured, provenance-preserving handoffs

- **Status:** `PROPOSED`
- **Priority:** P0
- **Decision:** Every AI handoff carries a typed task packet: objective, constraints, relevant
  context, evidence references, artifacts, unresolved questions, expected next action, and
  completion criteria. The project context ledger records the source and scope of every inherited
  claim or decision.
- **Recommendation:** Approve.
- **Why now:** Reliable handoffs and shared provenance are prerequisites for useful multi-AI work.
- **Tradeoff:** Structured packets add schema and persistence work before the flashy collaboration
  features appear.
- **Owner notes:** _Mike: add note here._
- **Decision date:** —

### PM-D008 — Isolate agents and grant task-scoped capability leases

- **Status:** `PROPOSED`
- **Priority:** P0
- **Decision:** Each worker receives only the paths, tools, network access, credentials, and time
  window required for its assignment. Builders use an isolated Git worktree or snapshot when
  practical. Researchers and Verifiers start read-only. Permissions do not silently inherit from
  MASTER, and agents cannot approve one another's escalation.
- **Recommendation:** Approve.
- **Why now:** Multiple agents sharing a mutable workspace and full credentials would multiply
  accidental damage and prompt-injection risk.
- **Tradeoff:** Isolation consumes storage and merging adds latency, but conflicts and rollback
  become tractable.
- **Owner notes:** _Mike: add note here._
- **Decision date:** —

### PM-D009 — Require independent verification for verified completion

- **Status:** `PROPOSED`
- **Priority:** P0
- **Decision:** Verifier receives the original objective, acceptance criteria, artifacts, diffs,
  tests, and source evidence in a separate context. It returns `PASS`, `FAIL`, or
  `INSUFFICIENT_EVIDENCE` and does not edit the work it judges. Project Master may say “verified
  complete” only after `PASS`; a Mike override remains possible and is logged.
- **Recommendation:** Approve.
- **Why now:** This operationalizes the Constitution's rule that attempted work is not automatically
  complete.
- **Tradeoff:** Verification adds latency and model load. The same model in a fresh context provides
  partial, not total, independence.
- **Owner notes:** _Mike: add note here._
- **Decision date:** —

### PM-D010 — Add a unified human Approval Center

- **Status:** `PROPOSED`
- **Priority:** P0
- **Decision:** Consequential requests from every AI wait in one Approval Center showing the
  requesting role and model, exact command or diff, target, reason, risk, downstream dependency,
  and rollback path. Initial actions are `Approve once`, `Reject`, and `Return to plan`. Permanent
  reusable grants wait until capability auditing is mature.
- **Recommendation:** Approve.
- **Why now:** Broad local capability becomes practical only when high-impact requests are visible
  and reviewable.
- **Tradeoff:** Classification must be tuned so harmless reads do not create approval fatigue.
- **Owner notes:** _Mike: add note here._
- **Decision date:** —

### PM-D011 — Gate autonomous parallel mutation behind readiness checks

- **Status:** `PROPOSED`
- **Priority:** P0
- **Decision:** Do not enable unattended parallel agents that mutate real user state until these
  exist and pass failure-path tests: durable task state, capability leases, workspace isolation,
  per-agent provenance logs, global cancellation, pre-mutation checkpoints, bounded resources,
  deterministic conflict handling, recovery, and independent verification. Before then, parallel
  work is read-only or uses user-approved isolated previews.
- **Recommendation:** Approve.
- **Why now:** This makes multi-AI credible without confusing concurrency with trustworthy autonomy.
- **Tradeoff:** It delays the flashiest mode while building the foundation that keeps user data safe.
- **Owner notes:** _Mike: add or remove readiness criteria here._
- **Decision date:** —

### PM-D012 — Treat project control files as first-class artifacts

- **Status:** `PROPOSED`
- **Priority:** P1
- **Decision:** Recognize `PROJECT.md`, `ideas.md`, `approvals.md`, `tasks.md`, and `DECISIONS.md`
  inside a project and surface them in the UI. SQLite remains the runtime store. Begin read-only;
  never silently overwrite a manually edited Markdown file.
- **Recommendation:** Approve as a phased feature.
- **Why now:** Human-readable control files make planning portable, Git-friendly, inspectable, and
  useful outside Project Master.
- **Tradeoff:** Bidirectional synchronization could create two sources of truth, so write-back must
  wait for explicit conflict rules.
- **Owner notes:** _Mike: add note here._
- **Decision date:** —

### PM-D013 — Decide when Linux becomes a first-class build target

- **Status:** `PROPOSED`
- **Implementation note:** The v0.3 beta candidate now includes portable sidecar tooling and a
  locally built, checksummed Fedora x86-64 RPM/AppImage; an earlier packaged AppImage candidate
  completed a startup smoke test. The exact regenerated artifacts, Ubuntu validation, installed RPM
  acceptance, and public Linux release approval remain incomplete. This implementation does not
  change the proposal's formal status.
- **Priority:** P1
- **Decision:** Keep Windows as the first public release target, but begin Linux development parity
  during the multi-AI foundation work instead of waiting for Windows stable. Add portable sidecar
  build scripts and a Fedora/Ubuntu smoke-test path before public Linux packaging.
- **Recommendation:** Approve.
- **Why now:** Mike actively develops on Linux, and early portability will expose Windows-only
  assumptions before they spread through the orchestration layer.
- **Tradeoff:** Cross-platform work slows near-term Windows features and expands the test matrix.
- **Owner notes:** _Mike: add note here._
- **Decision date:** —

### PM-D014 — Dogfood the Creator workspace first

- **Status:** `PROPOSED`
- **Priority:** P1
- **Decision:** Implement the Creator station as the first showcase workspace, using Mike's real
  YouTube workflow and a visible Researcher → Creator → Critic/Verifier team. Keep all behavior
  declarative so General, Research, Dev, and Operations stations can reuse it.
- **Recommendation:** Approve.
- **Why now:** Mike can test it daily and demonstrate a concrete multi-AI workflow publicly.
- **Tradeoff:** The product could overfit to one creator unless the underlying contracts remain
  generic.
- **Owner notes:** _Mike: add note here._
- **Decision date:** —

### PM-D015 — Add a first-class multi-AI run data model

- **Status:** `PROPOSED`
- **Implementation note:** The v0.3 beta candidate now persists projects, team runs, role
  assignments, activity events, artifacts, and approval records. The full proposed task graph,
  typed context/handoff model, and independent verification verdict contract must not be inferred
  from that partial implementation. Mike's formal decision is still required.
- **Priority:** P0
- **Decision:** Add versioned, project-scoped records for projects, runs, role instances, task graph
  nodes, event history, context packets, handoffs, artifacts, approvals, and verification verdicts.
  Keep the run/event history append-only where practical so a mission can be reconstructed and
  audited.
- **Recommendation:** Approve before building visible agent orchestration.
- **Why now:** When proposed, the store had conversations, memory, claims, and evidence but no
  durable representation of a multi-AI mission. Completing the remaining contracts avoids
  hard-coding temporary orchestration behavior into the UI.
- **Tradeoff:** Data migrations and schemas delay visible features, but avoid rebuilding the
  orchestration layer later.
- **Owner notes:** _Mike: add note here._
- **Decision date:** —

### PM-D016 — Require a backend security gate before broad agent capability

- **Status:** `PROPOSED`
- **Implementation note:** The v0.3 beta candidate now has a per-launch authenticated loopback
  session, restrictive desktop CSP, request-scoped mutation authorization, bounded terminal
  execution, and cancellation paths. Worker-specific isolation, capability leases, and complete
  revocation tests remain future work, so this proposal still awaits Mike's formal status decision.
- **Priority:** P0
- **Decision:** Before worker AIs receive shell, network, or broad filesystem access, add a
  per-launch authenticated loopback session, a restrictive desktop content-security policy,
  interruptible/cancellable tool execution, worker-specific memory/profile isolation, and
  failure-path tests for revoking every active capability lease.
- **Recommendation:** Approve.
- **Why now:** When proposed, the alpha used a fixed unauthenticated loopback service, had no
  restrictive CSP, and could not interrupt a tool already executing. Multi-AI would multiply the
  impact of any remaining gaps.
- **Tradeoff:** This is internal plumbing rather than a headline feature, but it is the boundary
  between a compelling demo and trustworthy local autonomy.
- **Owner notes:** _Mike: add note here._
- **Decision date:** —

## Decision log

Move decided entries here without deleting their rationale. Keep the newest decisions first and link
any superseding decision.
