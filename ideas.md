# Project Master Ideas

**Status:** Living concept backlog
**Last updated:** 2026-07-27

These ideas are directions to explore, not implementation commitments.
[`DESIGN_BRIEF.md`](DESIGN_BRIEF.md) preserves the original MVP brief; current implementation and
release scope live in [`ProjectMaster-v0.1.0/ROADMAP.md`](ProjectMaster-v0.1.0/ROADMAP.md) and
[`CHANGELOG.md`](CHANGELOG.md). Concrete choices that need Mike's decision belong in
[`approvals.md`](approvals.md), and only decisions he formally marks `APPROVED` are binding.

## Multi-AI north star

Project Master is not one chatbot with tools. It is a **local-first multi-AI command center** where
MASTER coordinates a user-owned team of AIs. Specialists investigate, build, create, and operate;
an independent verifier checks their work; the human remains the final authority.

**One command. Many minds. One accountable result.**

Multi-AI must be obvious in the product identity, onboarding, workspace, activity history, and final
results. It should never be hidden behind a single chat transcript or reduced to decorative agent
avatars. Every AI role needs a real assignment, selected model, bounded context, permissions,
deliverable, provenance, and completion test.

The v0.3 beta candidate begins making this architecture real through a bounded sequential council,
visible team activity, and durable run records. Uncontrolled parallel autonomy should still wait
until handoff integrity, source provenance, approval gates, recovery, and completion verification
are trustworthy.

## Operating model

```text
Mike
  │ objective, constraints, approvals, global stop
  ▼
MASTER — lead AI and user-facing orchestrator
  │ creates a visible task graph and bounded assignments
  ├──────────────┬──────────────┬──────────────┐
  ▼              ▼              ▼              ▼
Researcher     Builder        Creator        Operator
  └──────────────┴───────┬──────┴──────────────┘
                         ▼
                 Independent Verifier
                         │
                         ▼
             MASTER synthesis and completion receipt
                         │
                         ▼
                        Mike
```

MASTER owns the mission, delegation, integration, and final response. Specialist roles are
functional contracts rather than fictional personalities. Workers should not recursively create
unbounded swarms. Mike can inspect any role, change its model or assignment, stop the whole run, and
resolve disagreements.

## The MASTER Team

### 1. Role contracts

Each reusable AI role should declare:

- Responsibility and boundaries
- Expected deliverable
- Available tools
- Context and file access
- Permission ceiling
- Assigned model and fallback
- Time, token, and retry budget
- Completion criteria

The initial roster could be:

- **MASTER / Lead** — understands the objective, plans, delegates, integrates, and speaks to Mike.
- **Researcher** — gathers sources and evidence; read-only by default.
- **Builder** — creates or changes files inside an isolated task scope.
- **Creator** — develops scripts, concepts, media plans, and presentation-ready artifacts.
- **Operator** — runs approved workflows and monitors long-running work.
- **Verifier** — independently checks claims, artifacts, tests, and completion evidence.

The interface can show the full roster without pretending every model is loaded or running at once.

### 2. Team Strip

Keep the active team visible in the workspace header or left rail. Every role shows:

- Role and selected model
- Current assignment
- `Waiting`, `Working`, `Blocked`, `Reviewing`, or `Done` state
- Access scope and execution mode
- Elapsed time and resource use

Selecting a role opens its assignment, evidence, activity, and output without leaving the main
mission.

### 3. Visible work lanes

Show collaboration as real lanes connected by dependencies. A lane contains its objective,
activity, handoffs, artifacts, blockers, and verification state. Parallel work is shown only when it
is genuinely parallel.

Examples:

```text
MASTER → Researcher → Writer → Verifier → MASTER
MASTER → Builder ───┐
MASTER → Tester ────┴→ Verifier → MASTER
```

The final answer links back to the lanes that produced each important claim or artifact.

### 4. Structured handoffs

Agents should not hand one another vague conversation summaries. A handoff packet should contain:

- Objective and acceptance criteria
- Constraints and permissions
- Relevant user instructions
- Evidence and source references
- Files or artifacts produced
- Assumptions and unresolved questions
- Expected next action

Every inherited claim keeps its origin. If evidence or a decision changes, Project Master can
identify which downstream work may now be stale.

### 5. Shared context ledger

Do not copy the entire conversation into every model. Maintain a project-level ledger for:

- Confirmed decisions
- User instructions and scope
- Evidence and claims
- Artifacts and file versions
- Assumptions and open questions
- Agent handoffs and corrections

Each role receives only the relevant slice. The UI should show exactly what context was shared and
allow Mike to pin, exclude, or correct it.

### 6. Model per role

Let Mike assign different installed Ollama models to different roles. A long-context model may suit
research, a tool-reliable model may suit operations, a creative model may suit scripting, and a
skeptical model may suit verification.

Assignments should support:

- Pinned role-to-model mappings
- Explicit fallback models
- Hardware-aware recommendations
- Sequential loading when VRAM is limited
- A measured local benchmark instead of provider marketing claims

Multi-AI should still work when only one model is installed by using separate role prompts,
contexts, and verification passes. The UI should be honest that this is contextual separation, not
full model independence.

#### Thinking-mode discovery and controls

Add a model-capability record that distinguishes:

- no thinking support;
- boolean thinking control (`off` / `on`);
- level-based control (`low`, `medium`, `high`, and `max` only when supported);
- thinking support whose available controls are still unverified.

Prefer explicit provider metadata, but do not treat missing metadata as proof that a converted or
custom model cannot think: the local 26B SuperGemma emitted a separate thinking trace even though
`/api/show` omitted the capability. When metadata is absent, broad, or contradicted by a response,
keep the precise modes labeled unknown until a bounded local capability probe confirms accepted
values. Persist the detection source and Ollama version, expose the result in model settings and
role assignment, and let each role choose a supported mode or inherit a safe default. Never infer a
mode solely from a display name and present that guess as verified.

#### Inference-settings drop-downs in chat (Mike, 2026-07-27, during the v0.3.0 GUI pass)

The desktop should expose the core inference settings as drop-down controls near the chat input
instead of burying them in config:

- **Token context** (`num_ctx`): selectable sizes bounded by what the selected model and local RAM
  actually support, with the active value still displayed the way the footer shows it today.
- **Thinking mode**: off / low / medium / high / max, offering only the levels the capability
  record above has verified for the selected model; unsupported levels stay hidden rather than
  silently downgraded.
- Related per-conversation settings that currently require config edits (max response tokens,
  temperature) belong in the same control cluster.

Selections apply per conversation, persist with it, and fall back to today's conservative defaults
when unset. Backend chat requests need optional override fields for these values, validated against
the same bounds the UI enforces. Target: v0.3.1 — intentionally out of scope for the v0.3.0 RC.

### 7. Independent verifier

The Verifier should receive the original objective, acceptance criteria, produced artifacts,
diffs, test output, and source evidence. It should not receive or rely on the producer's hidden
reasoning or self-assessment.

It returns:

- `PASS`
- `FAIL`
- `INSUFFICIENT_EVIDENCE`

The Verifier does not silently repair the work it judges. Disagreement remains visible so MASTER or
Mike can request a revision, accept an explicit override, or stop.

### 8. Accountable synthesis

MASTER's final response should distinguish:

- What the team agrees on
- Where specialists disagree
- What was directly verified
- What remains uncertain
- Which role produced each important artifact
- What changed on the machine
- What can be reversed

Multi-AI should increase scrutiny and capability, not manufacture false consensus.

## Recommended first multi-AI vertical slice

### Verified Team Run

Build the smallest experience that is genuinely multi-AI:

1. Mike gives MASTER one concrete objective.
2. MASTER creates a task contract with constraints and acceptance criteria.
3. Mike reviews the visible task graph.
4. A **Builder** completes one bounded assignment.
5. A separate **Verifier** checks the result against the original criteria.
6. MASTER reports the outcome with a completion receipt and any disagreement.

Run the roles sequentially at first. This proves orchestration, role-specific contexts, structured
handoffs, persistence, cancellation, and independent verification without pretending the safety
problems of parallel mutation are solved.

The slice is successful only if:

- The selected model, task, status, and permissions for each role are visible.
- The task and handoff records survive an application restart.
- A global Stop cancels the active role and blocks downstream work.
- The Verifier cannot approve its own output.
- Project Master distinguishes attempted, partially complete, and verified complete.
- The activity view shows plans, tool events, evidence, and results—not raw chain-of-thought.

## Product surfaces

### Mission Control shell

Evolve the current conversation-focused layout into a project-and-mission workspace:

- Left: projects, conversations, and AI Team roster
- Center: conversation, mission brief, or active artifact
- Right: work lanes, activity, approvals, evidence, and results
- Bottom status strip: models, access scope, execution mode, pending approvals, and global Stop

Borrow Hermes One's cohesion and ambition, not its code, assets, branding, or exact visual design.
Project Master should remain denser, more inspectable, more local, and more evidence-driven.

#### Mission view for the center pane (Mike, 2026-07-27, during the v0.3.0 GUI pass)

The right side already reads as an autonomous operating surface, but the center pane still renders
a Team run as chat bubbles, so the product's core idea lives in the sidebar. When a Team run is
active, the center pane should become a mission document instead of a transcript:

```text
Mission
──────────────

Goal
Build a two-page article

Status
Researching...
██████████░░

Artifacts
✓ outline.md
✓ references.json
✓ draft.md
✓ final.md

Council Decisions
• Critic requested rewrite
• Verifier approved
• Lead merged changes
```

Dispatching work to a team should feel different from talking to an AI. The chat transcript remains
available (Direct mode, or a toggle), but a running mission leads with goal, live status and
progress, produced artifacts, and council decisions — all of which already exist as run events,
role activity, and artifact ledger records; this is a rendering change, not new data. The council
decision log must stay at the operational-summary altitude the Run Rail already enforces, never
private drafts or chain-of-thought.

**Status:** a first version shipped in the v0.3.0 RC on Mike's direction (2026-07-27): mission
document with goal, phase status and progress, council decisions, tool activity, delivered
synthesis, and a Mission/Transcript toggle. Still open for later versions: a durable artifacts
checklist fed by the run artifact ledger, and richer per-phase progress once council plans expose
their intended role count up front.

### Run Rail

Show the current run as:

`Understand → Plan → Delegate → Act → Verify → Deliver`

Each event records the responsible role, selected model, timestamp, tool request, affected target,
result, and verification state. It should expose useful operational summaries without claiming to
show private model chain-of-thought.

### Approval Center

All consequential requests from every AI converge in one queue. Each item shows:

- Requesting role and model
- Exact command, diff, or action
- Target and scope
- Reason and downstream dependency
- Risk and reversibility
- Rollback plan

Mike can approve once, edit, reject, or return the request to planning. Reusable permissions should
remain narrowly scoped and auditable.

### Project Binder

A project should be more than a folder or chat collection. Give it a durable local binder:

- Mission and current objective
- Decisions and approvals
- Conversations and AI runs
- Working files and artifacts
- Evidence and open questions
- Project-specific memory

Recognize human-readable files such as `PROJECT.md`, `ideas.md`, `approvals.md`, `tasks.md`, and
`DECISIONS.md`. Start by rendering them read-only. Never silently overwrite manually edited project
files.

### Evidence Lens

Make the existing epistemic engine visible. Selecting a claim should reveal:

- Supporting, contradicting, and contextual evidence
- Source lineage and shared-source chains
- Confidence and current status
- Missing evidence
- What would change the assessment
- Which AI introduced or evaluated the claim

This is a major Project Master differentiator and a natural foundation for multi-AI research.

### Context Inspector

Before a run, let Mike inspect what each AI will receive:

- Conversation turns
- Attached files
- Recalled memories
- Project decisions
- Communication rules
- Role instructions

Context becomes user-owned state instead of invisible model plumbing.

### Completion receipts

Every work-oriented run ends with a durable receipt:

- Objective
- Roles and models used
- Actions taken
- Files or settings changed
- Evidence and tests checked
- Verifier result
- Remaining uncertainty
- Recovery options

Project Master should never display “Done” when it only attempted the task.

### Recoverable runs

Before consequential writes, create a scoped checkpoint of affected files plus Git status and diff
when available. Offer **Undo run** where reversal is practical. Never imply that a checkpoint can
reverse external effects such as sent messages or remote deletions.

### Local Model Bench and resource governor

Benchmark installed models for tool calling, instruction following, context reliability, speed,
VRAM use, and verifier performance. A local scheduler should queue roles, cap concurrency, unload
models when needed, and prevent a multi-AI run from overwhelming Ollama or the machine.

### Council on demand

Do not fan every prompt out to many AIs. MASTER should suggest a council when the objective is
high-impact, ambiguous, research-heavy, or benefits from competing approaches. Mike can also invoke
it manually.

This keeps simple work fast while reserving multiple minds for work that earns the extra latency.

### Workspace stations

Turn saved layouts into operational stations:

- **Command** — balanced general work
- **Research** — claims, evidence, sources, and document comparison
- **Dev** — files, diffs, terminal, tests, and verifier output
- **Creator** — scripts, media plans, previews, and publishing assets
- **Operations** — jobs, logs, approvals, and completion receipts

Stations remain editable, local, and declarative.

### Creator Studio

Dogfood Project Master on Mike's real workflow:

- Video concept and hook council
- Researcher-backed outline
- Script Builder and Critic passes
- Asset checklist and production board
- Recording-safe status view for OBS
- Private-path and secret redaction for streams

This can become the most distinctive public demonstration of multi-AI work without making the core
architecture creator-specific.

### Runbook Forge

Turn a successful team run into an open `SKILL.md` runbook containing its roles, steps, tools,
permissions, inputs, expected outputs, and verification checks. Require review and an in-app test
before saving.

## Multi-AI rollout

### Shipped foundation — v0.3 beta candidate

- Made the multi-AI identity explicit in the product, release copy, Team mode, and installed-model
  catalog.
- Added capability-aware role assignments, a bounded sequential council, and one lead-owned final
  synthesis.
- Surfaced model assignments, worker state, tool summaries, synthesis, and delivery checkpoints in
  the Team Strip and Run Rail without exposing private reasoning.
- Added durable projects, team runs, artifacts, activity events, and Project Binder provenance.
- Added per-launch backend authentication, a restrictive desktop CSP, request-scoped mutation
  authorization, cancellation paths, and resource-aware sequential Ollama use.
- Kept worker mutation and claims of independent verification out of the shipped council until the
  stronger handoff, isolation, and verifier contracts below are complete.

### Next — Controlled collaboration

- Typed task packets, handoffs, completion receipts, and explicit task-graph dependencies
- Dedicated Researcher, Builder, Creator, and independent Verifier workflows
- Read-only parallel research
- Route every consequential tool request through the Approval Center and add capability leases
- Isolated Builder workspaces and pre-mutation checkpoints
- Visible disagreement and revision loops
- Extend the shipped Project Binder into an Evidence Lens and provenance graph

### Later — Controlled autonomy

- Parallel agents that mutate isolated state
- Resumable background missions
- Dynamic role creation
- Automatic conflict handling and recovery
- Remote workers and optional cloud models
- Hands-off multi-AI runs only after the readiness gate passes

## Architecture groundwork

The v0.3 beta candidate now has a deterministic sequential team orchestrator, scoped role
assignments, durable projects and runs, an activity ledger, artifacts, approvals, and authenticated
desktop/backend communication. It does not yet claim the complete task graph, typed handoff,
capability-lease, isolated-worker, or independent-verification model described in this backlog.

Continue the architecture in these directions:

- Keep the deterministic Python orchestrator as the owner of run state; models may propose plans but
  must not declare themselves complete.
- Extend durable run records into a persistent task graph with dependencies, attempts, blockers,
  budgets, rollback, and required completion evidence.
- Preserve append-oriented run and event history so missions remain reconstructable and the visible
  activity UI stays truthful.
- Finish project/run/agent scoping for claims, evidence, context, and memory instead of allowing
  worker state to become globally shared by default.
- Worker outputs are patches, artifacts, evidence, and typed handoffs—not uncontrolled edits to one
  shared folder.
- Keep Ollama inference serialized by default to avoid model-loading and VRAM thrash; safe tool work
  can become parallel separately.
- Extend the shipped per-launch authentication, CSP, mutation gate, and cancellation paths with
  worker-specific isolation, capability leases, and tested revocation before broadening autonomy.

## Non-goals

- A decorative “AI office” with avatars that do not represent real work
- Multiple agents repeating the same prompt to create an illusion of consensus
- Hidden agent actions or invisible permission inheritance
- Raw chain-of-thought as a product feature
- A shared memory pool with no provenance or scope
- Unlimited recursion, concurrency, retries, or spending
- “Verified” labels based on the producing agent's confidence
- Cloud requirements, forced accounts, or telemetry by default

## Product filter

Every new surface should answer at least one of these questions:

1. Which AI is doing what?
2. What context and authority does it have?
3. Why should Mike trust this result?
4. What changed, and who changed it?
5. What is blocked or waiting for approval?
6. How can the result be reversed, corrected, or reused?

If a feature answers none of them, it probably does not belong in the cockpit.
