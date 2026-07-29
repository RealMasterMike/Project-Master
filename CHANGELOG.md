# Changelog

All notable Project Master desktop releases are recorded here.

## Unreleased — source after the 0.4.0 package

These changes landed **after** the 0.4.0 artifacts were built. The 0.4.0 AppImage and RPM described
below do not contain them.

### Fixed

- ComfyUI job tools no longer accept a job ID from outside the conversation's own project.
  `comfy_run_status`, `comfy_run_cancel`, and `comfy_run_artifacts` passed a model-supplied job ID
  straight to service accessors that resolve a job by ID alone, so a chat scoped to one Creator
  project — or a rootless chat — could poll, read the artifacts of, or cancel another project's job
  by naming its ID. Every job-targeting tool now requires the job's project to match the tool
  registry's scope, and a rootless chat owns only rootless jobs. This bounds what the model can
  reach; the local HTTP API is unchanged. A dedicated regression for this belongs with the
  outstanding chat-to-generation end-to-end test and is not yet written.

### Changed

- Polished the workspace navigation menu without changing its routes, structure, or labels.
  Navigation decision logic moved into a plain, unit-tested module; the menu gained clearer
  spacing, readable inactive items, visible focus and pressed states, a raised stacking order,
  viewport-relative sizing, and text truncation so long labels cannot collide with the existing
  right-side tags. Reduced motion is honoured by the existing global rule.
- Added a compact MASTER status section to that menu reporting real local model count, tool status,
  and application version. Values are read once when the panel first opens rather than polled, and
  every unread or unavailable value is reported as such instead of being replaced by a guess.
- Removed absolute developer paths from the Linux packaging guide and the changelog.

### Verification

- Backend 445 passed, 1 skipped; Ruff clean.
- Frontend 102 passed (15 added for the navigation module); TypeScript and the production Vite
  build passed. Main chunk approximately 567 kB after minification, a pre-existing advisory.
- Rust desktop 15 passed; `cargo fmt --check` and strict Clippy clean.
- Packaging tests 29 passed; packaging preflight passed; `npm audit --omit=dev` reported 0
  vulnerabilities.
- The packaged-window visual check of the navigation menu at the 1024-pixel minimum width has not
  been performed and is not claimed.

## 0.4.0 BETA — 2026-07-28

Private local dogfood build. **Not published.** No signed updater feed was provisioned, no GitHub
Release was created, and no tag was pushed. The configured updater endpoint still returns 404 and
no signing secret was available, so publishing a feed would have advertised an update that cannot
install. Tauri signature verification remains configured for a later signed release.

The previous verified 0.3.0 package is preserved locally as a backup
(SHA-256 `1edd58ae96ec21a4e32be10294e38fc9c71ebafe52d3d7cbd8a6780872be6469`).

**The headless Linux acceptance gate did not pass: 35 checks passed, 5 failed.** The Desktop
launcher was deliberately not repointed at this build. Two failures are GPU contention from other
resident processes, two are a 16.8 GB model that cannot load on an 8 GB card, and one is an
adversarial unavailable-tool prompt that the shipped default model fails identically with the old
and new system prompts. Per-model measurements were recorded in the maintainer's private release
notes.

### Added

- Added Chroma1-Flash as the automatic curated Text-to-Image and Image-to-Image default. Its
  publisher retrained the model on an unfiltered dataset and documents it as fully uncensored
  (Apache-2.0, 8.9B, FLUX.1-schnell derived), which meets the curated-default provenance rule
  without relying on a third-party finetune claim. RealVisXL V5.0 remains a curated, selectable
  workflow and is not relabelled manual/unverified. Both bundled Chroma workflow documents were
  executed directly against ComfyUI on an 8 GB GPU with benign prompts and produced valid
  1024x1024 PNGs (93 s text-to-image, 108 s image-to-image). Negative prompts were confirmed
  effective at guidance 2.5 and are documented as inert at guidance 1.0 rather than presented as a
  control that does nothing. Project-Master-mediated Chroma generation is not yet validated.
- Added literal conversation search, bounded workspace file search with secret/path exclusions,
  consent-gated public-page reading, and optional SearXNG search. Web requests use a pinned,
  revalidated public address, reject unsafe redirects and compressed/unbounded responses, bypass
  environment proxies, and keep SearXNG SafeSearch disabled.
- Added per-message speech playback backed by the local Voice Studio pipeline, including cloned
  voice selection, auto-speak, playback speed, cross-chunk seeking, and stop controls.
- Added bounded automatic continuation when a model stops mid-response, with separate limits for
  tool rounds and continuation attempts.
- Added validated Creator projects and a project-scoped content-idea workspace that turns
  structured briefs into durable, review-only Creator Spark proposals and media-brief candidates.
- Added a project-scoped image, video, and audio catalog with bounded raw uploads,
  content-signature validation, content-addressed private storage, verified inline reads, and
  automatic cataloging of verified outputs from project-scoped ComfyUI jobs.
- Added automatic viewport-aware image, audio, and video previews with abort-safe loading, object
  URL cleanup, integrity checks, and a persistent reduced-bandwidth preference.
- Added project-media search over visible metadata, deterministic newest/oldest/name/size sorting,
  search-aware type counts, and distinct loading, empty-library, and no-match states.
- Added an AI-first Creator surface with separate Text-to-Image, Text-to-Video, Image-to-Image, and
  Image-to-Video modes. Manual non-destructive trimming remains available under Utilities rather
  than being presented as the editor.
- Added a verified project-image bridge for ComfyUI. Only an owned Creator Media asset ID may bind
  to a `LoadImage.image` input; the backend rechecks project ownership, dimensions, size, signature,
  and SHA-256 before staging it in Project Master's fixed ComfyUI namespace.
- Added immutable ComfyUI workflow purposes for general, image, video, and audio generation, plus
  explicit node/model-resource compatibility checks and a mandatory live preflight before upload,
  durable job creation, or queue submission.
- Added four reproducible bundled Creator defaults: RealVisXL V5.0 Text-to-Image and Image-to-Image,
  plus Wan 2.2 LightX2V 4-Step Uncensored Text-to-Video and Image-to-Video. Bundled revisions are
  deterministically seeded and do not overwrite a later user rejection.
- Added Direct-chat image attachments for verified Creator Media images. Up to three bounded images
  are reverified and sent only in the current Ollama request; image bytes are not written to chat
  history, run events, or the database.
- Added bounded ComfyUI chat tools for listing connections/workflows, checking compatibility,
  queueing an approved project-scoped revision, monitoring it, cancelling it, and reconciling
  verified generated assets into Creator Media.
- Added a dedicated Settings destination in the hamburger menu with the installed version, release
  channel, automatic-check cadence, manual **Check for updates**, signed update progress, and an
  explicit update-and-restart action.
- Added persistent interface density, text scale, motion, preview, default generation type, and
  explicit vision-model preferences. Session-only mutation and web permissions remain outside
  persistent settings.
- Added trim-point actions from the source preview playhead and bounded ±0.10-second in/out nudges
  while retaining the numeric bounds as the authoritative edit.

### Changed

- Removed all generated AI personalization from the assistant system prompt at the user's request.
  The prompt now carries only the instructions tool calling depends on; persona, tone direction, the
  scripted first-session introduction, thinking-mode policy, and the imposed epistemics template
  were deleted. The adaptive communication profile, which silently rewrote the model's own
  instructions from observed user style, is no longer injected into the prompt. Style observation
  code is retained and re-enabling it is a single documented line. Memory and interpretation context
  are unaffected.
- Reduced frozen-sidecar contents to exactly the curated workflow definitions instead of the whole
  examples directory. The filename list is read from the backend's authoritative tuple so the
  packaging script and the runtime loader cannot drift apart.
- Creator's workflow picker now marks which curated default is chosen automatically, because more
  than one curated default can exist for a single operation.
- Added lead-model triage to Team runs so simple follow-ups can skip unnecessary specialists while
  substantive requests still receive the relevant council; failed or malformed triage falls back
  to the complete team.
- Increased the shipped Ollama context-window default to 65,536 across engine configuration,
  examples, documentation, and direct client construction.
- Changed the shipped conversational model default from the absent `qwen3:8b` tag to the installed,
  publisher-documented
  `hf.co/TrevorJS/gemma-4-E4B-it-uncensored-GGUF:Q4_K_M` revision.
- Polished the command workspace, chat controls, Mission/Run Rail status language, and Voice Studio
  presentation for clearer live, blocked, skipped, and completed states.
- Split Creator/ComfyUI and reusable dashboard primitives out of the main workspace component, then
  organized Creator around focused Ideas, Media, Create, AI Edit, Utilities, and Workflows
  sections.
- Made Creator navigation and provenance more direct: generated jobs can open their owning
  project's Media library, trim assets show their source/range/recipe, and Creator copy now reflects
  the prompt-first creation and AI-editing experience.
- Changed ComfyUI/Ollama GPU ownership at integration boundaries: interactive Ollama use waits for
  reachable Comfy queues to become idle and asks ComfyUI to release cached models; active queues
  retain ownership, optional offline profiles do not block chat, and Comfy submission first unloads
  only the Ollama runner tracked by Project Master.

### Fixed

- Accepted valid Fedora `audio/vnd.wave` WAV imports by relying on the backend's RIFF/WAVE parser
  instead of rejecting recordings from an unreliable browser MIME hint.
- Made GPU-backed voice rendering wait for the shared inference lease, report actionable resource
  conflicts, and clear request-scoped chat leases left behind by a hard process termination.
- Played every chunk of long synthesized messages instead of stopping after the first artifact.
- Prevented wrong-architecture GStreamer plugins from entering Linux packages and ensured packaged
  playback can use compatible host plugins instead of crashing the WebKit process.
- Restored the composer to the visible viewport after navigation cleanup by aligning the application
  grid tracks with its actual children.
- Hardened desktop backend startup so a new window does not terminate a backend owned by another
  live Project Master GUI, while retaining authenticated orphan recovery after a crash.
- Replaced arbitrary first-tag fallback for a missing configured Ollama model with the installed
  catalog's capability-aware recommendation while preserving an already valid chat selection.
- Rejected the first advertised-vision candidate after three reproducible gibberish image results
  and replaced the automatic image-analysis default with the physically passed
  `lukey03/qwen3.5-9b-abliterated-vision:latest` tag-plus-manifest identity.
- Improved persisted voice-job recovery and cleanup, message-audio playback state, and Linux media
  packaging checks.
- Recovered once when an Ollama turn consumes its output budget in private thinking, removed tagged
  reasoning traces, and repaired clear untagged model deliberation before it can be displayed or
  stored.
- Preserved Ollama completion reasons through streaming so token-limited and unexpectedly dropped
  responses use the existing bounded continuation path even when the visible text ends cleanly.
- Disabled model thinking where the provider supports it, requested a bounded tool-free recovery
  when a turn contains no visible answer, and kept continuation/retry attempts separately bounded.
- Kept empty-state recovery notices within the initial viewport on compact and offline layouts.
- Aligned Creator's default ComfyUI profile with the persisted `local-default` profile and exposed
  live connection health, device counts, and available node-type counts in the connection panel.
- Kept manual updater checks and installs safe across Settings navigation by closing unused update
  handles, allowing an approved install to finish, and suppressing stale state updates after the
  workspace unmounts.
- Cleared stale web-tool metadata before each Settings refresh and report unknown status on backend
  failure instead of mislabeling page reading, SearXNG, or SafeSearch state.
- Preserved keyboard focus across hamburger/context menus, compact Run Rail open/close, Creator
  section jumps, compatibility rechecks, idea decisions, job cancellation, retries, and cleared
  Media searches.
- Kept prior Team activity reachable after switching to Direct mode and retained the correct
  three-column/overlay layout while its Run Rail is present.
- Added focused live announcements for connection checks, ComfyUI job state, Creator idea runs,
  speech rendering, and the latest Team-run checkpoint without replaying entire activity lists.
- Increased undersized speech, Mission, and Run Rail targets and applied compact toolbar labels at
  the packaged application's 1024-pixel minimum width.

### Security

- Kept all web access inert until per-conversation consent is present. Public-page reading works
  without a search provider; SearXNG search additionally requires a credential-free configured
  endpoint. Local conversation and file search remain local-only.
- Validated media signatures independently of filenames and browser MIME declarations, bounded
  streamed imports, stored bytes outside project roots, and verified SHA-256 on every served read.
- Rejected incompatible ComfyUI workflows and missing fixed loader resources during a live
  preflight before staging project media, creating a durable job, or submitting anything to
  ComfyUI.
- Kept arbitrary filesystem paths out of image-driven workflows and retained source-asset,
  content-hash, staged-name, binding, workflow, job, and project provenance without persisting image
  bytes in a workflow request.
- Kept ComfyUI custom nodes disabled globally while allowing only the pinned `ComfyUI-GGUF` loader
  required by the verified low-VRAM video workflows.
- Kept manual update checks non-installing and routed every approved install through the existing
  Tauri signature-verification and relaunch path.
- Restricted automatic Direct chat, image analysis, Team/Dream model assignment, and Creator
  workflow defaults to exact curated identities with publisher evidence. Ollama identities also
  require the physically tested tag, manifest digest, and path-specific purpose after a fresh
  execution-admission catalog read; a suggestive model name, stale cache, or mutable tag is
  insufficient. Model-less Direct requests fail closed when no curated match exists. User-imported
  workflows and explicitly selected local models remain manual/unverified, and Team UI no longer
  offers models its automatic council will reject.

### Documentation

- Expanded the engine README and local API reference for Creator projects, ideas, project media,
  AI creation/editing, transient image analysis, web tools, GPU handoff, workflow purposes,
  compatibility checks, image staging, and generated-asset cataloging.
- Replaced the stale session handoff with a prioritized, acceptance-test-driven overnight queue for
  the next implementation agent; retained the original incident log in the maintainer's private
  working notes.
- Documented exact model repositories, immutable revisions, file sizes, SHA-256 hashes, licenses,
  runtime dependencies, hardware-fit evidence, and the distinction between publisher-labeled NSFW
  and Uncensored assets.

### Verification

- Backend: the settled source run passed 441 tests with 1 skipped; Ruff was clean.
- Frontend: the settled source run passed 85 tests; TypeScript and the production Vite build passed
  after digest-bound curated/manual model labels and the verified vision-default change. Vite
  retained a non-blocking main-chunk advisory at 564.86 kB after minification.
- Rust desktop: 15 passed; formatting and strict Clippy passed.
- Packaging scripts: 22 passed and the prerequisite preflight passed. The frozen-sidecar and exact
  AppImage/RPM verification evidence below belongs to the earlier package, not the current source
  tree.
- Production dependency audit reported zero vulnerabilities.
- Real FFmpeg trim acceptance passed, including an odd-dimension input, and live ComfyUI 0.28.0
  remained healthy with an idle queue.
- A benign physical smoke test of the publisher-labeled Wan LightX2V Uncensored Text-to-Video stack
  completed both expert stages on the local 8 GB GPU, produced a valid 384×224, 9-frame H.264 MP4
  in 41.02 seconds, and then succeeded through Project Master's durable job and verified-artifact
  path.
- Both pinned Wan I2V Q3_K_S experts matched their immutable-source sizes and SHA-256 hashes and
  appeared in live ComfyUI resource discovery. A benign direct four-step run produced a valid
  384×224, 9-frame H.264 MP4; an isolated Project Master Creator run then passed verified Media
  input, staging, durable job execution, artifact import, and generated-Media reconciliation. The
  app-owned artifact and Media copy were byte-identical at SHA-256
  `9f80d4bffa90bf0e1be7d9db4f174a2b39ffa0f2417944ce05710441205a8a82`.
- The exact RealVisXL V5.0 checkpoint was size/hash verified. A direct Text-to-Image generation and
  an isolated Project-Master-mediated Image-to-Image job both produced valid 512×512 outputs; the
  durable artifact and Creator Media catalog bytes matched.
- The exact shipped TrevorJS conversational default completed visible 65K-context chat and a real
  calculator tool round after ComfyUI cache release, then unloaded cleanly.
- The exact `lukey03/qwen3.5-9b-abliterated-vision:latest` model coherently described the same
  benign 256×256 fixture through minimal Ollama and the authenticated Creator Media chat path at
  65K context. It emitted visible text without private thinking, persisted no image bytes or asset
  ID, and unloaded cleanly.
- Live idle-Comfy handoff called the official memory-release endpoint successfully. A benign live
  `web_fetch` call also passed the pinned-address HTTPS/SNI path.
- The earlier AppImage is 140,704,248 bytes with SHA-256
  `1edd58ae96ec21a4e32be10294e38fc9c71ebafe52d3d7cbd8a6780872be6469`; the earlier RPM is
  28,316,478 bytes with SHA-256
  `379b3cb875c65c32216bbe12f9ab98a36e893966d91b2d4e1eb433cde1449b41`.

### Known limitations

- Project Master does not bundle or silently download multi-gigabyte model weights. Another
  installation must provide the documented exact files; only the four app-owned workflow
  definitions are seeded automatically.
- `ComfyUI-GGUF` describes GGUF LoRA patching as experimental. The exact Uncensored stack passed the
  local smoke test, but higher resolutions, longer clips, and broad prompt quality still need
  supervised acceptance on the target 8 GB GPU.
- The configured signed beta updater feed and Linux updater artifacts are not yet provisioned, so
  the Settings action truthfully reports the current feed failure but cannot deliver a Linux build
  until signed release infrastructure is completed.
- The Desktop AppImage currently under manual testing was built at 03:51–03:52 on 2026-07-28 and
  predates substantial Creator, vision, web, workflow, GPU-handoff, and Settings source changes. It
  remains the last fully verified package and must not be described as containing this Unreleased
  source or replaced until Mike finishes that test and requests a new build.
- The sidecar build currently copies the complete `examples/comfyui` directory. Before a new
  package, restrict bundled workflow data to the four curated defaults and prove manual/deprecated
  graphs, tests, caches, source maps, wrong-architecture binaries, user data, and secrets are absent.
- The local 0.4.0 packages are unsigned and unpublished. They identify as `0.4.0`, so they no longer
  collide with the published `0.3.0` release, but they must not be uploaded without completing the
  release gate below.

## 0.3.0 BETA RC — 2026-07-27

This entry describes the Linux daily-driver release candidate. It does not mean the candidate has
been uploaded or approved for public release; the release gate below still applies.

### Added

- Added an obvious multi-AI **Team** mode backed by the installed Ollama catalog. Project Master
  de-duplicates aliases for the same physical model, selects a capable lead, assigns bounded
  specialist roles, executes the council sequentially, and streams a user-facing Team Strip and
  Run Rail without exposing specialist drafts as chain-of-thought.
- Added durable **Projects** with local roots, run and artifact ledgers, and safe event summaries.
- Added the **Project Binder** local RAG path: bounded text/source indexing, secret and credential
  exclusions, content versioning and SHA-256 provenance, cited search results, and attachable
  retrieval context for Direct and Team chat.
- Added a unified tool inventory covering workspace inspection, calculator, time, memory, evidence,
  bounded terminal commands, Project Binder, Dream, ComfyUI, and Voice Studio operations.
- Added **Dream Lab** with explicit-source manual runs, custom recipes, provenance snapshots,
  resource-aware schedules, bounded catch-up, quiet hours, and a proposal-only Inbox requiring a
  human accept/reject decision. Scheduled Project Binder sources require explicit per-project
  consent, an indexed source, and a scoped recipe before a schedule can be enabled.
- Added **ComfyUI Creator** support for trusted connection profiles, immutable API-format workflow
  revisions, typed and bounded input bindings, exact-revision approval, owned jobs, cancellation,
  restart reconciliation, and checksum/provenance-preserving local output artifacts. The desktop
  gallery previews image, audio, and video outputs and prepares authenticated local downloads.
- Added **Voice Studio** with designed and attested reference-voice profiles, script projects,
  cancellable chunked jobs, the distribution eSpeak NG adapter, optional pinned Chatterbox setup,
  and checksum-verified downloadable audio artifacts.
- Added first-class Fedora packaging through host-native Python sidecars, Tauri RPM and AppImage
  targets, cross-platform Node build launchers, prerequisite checks, sidecar smoke tests, artifact
  verification, and local SHA-256 manifests.
- Added one reproducible headless Linux candidate gate that runs the exact backend staged in
  `master.AppDir` through isolated authentication/lifecycle, Ollama, tools, Binder, mutation,
  Dream, ComfyUI-offline, eSpeak, Chatterbox, and per-physical-model checks, then emits JSON and
  Markdown reports without launching the GUI or persisting a session token.
- Added dashboard workspaces for Projects, Approval Center, Dream, Creator, and Voice while
  preserving streaming chat, conversation history, layout customization, cancellation, and
  backend recovery.
- Added a **Mission view** for the center pane during Team runs: goal, live status with a phase
  progress bar, specialist completion counts, council decisions, tool activity, and the delivered
  synthesis as a mission document instead of chat bubbles. A Mission/Transcript toggle preserves
  the conversation view, and the mission surface renders only the same bounded run events the Run
  Rail already exposes — never specialist drafts or private reasoning.

### Changed

- Repositioned Project Master from a single-agent Windows alpha into a local-first multi-AI beta
  candidate while retaining Direct mode and the Windows build path. Windows v0.3.0 installers still
  require validation against the same accepted source state.
- Changed the default desktop experience to Team mode when a compatible local council is available.
- Changed release metadata across npm, Python, Cargo, Tauri, the browser title, and the desktop
  window to `0.3.0`.
- Changed beta update checks to the weekly policy and moved the configured updater endpoint away
  from the alpha channel. A signed beta channel still has to be created before public release.
- Changed Python packaging to bounded dependency versions verified across supported Python 3.11
  through 3.14 runtimes.
- Changed failed-chat retries to use the mutation authorization currently shown in the desktop
  instead of silently reusing consent captured by an earlier request.
- Changed Ollama requests to defensively disable private thinking for non-GPT-OSS models even when
  converted-model metadata omits the capability, and select GPT-OSS's lowest supported level,
  preventing hidden reasoning from silently consuming the entire bounded visible-response budget.
- Added switch-aware Ollama residency: Direct tool rounds can keep one model warm, while Team,
  Dream, and model changes explicitly unload Project Master's prior runner before loading another
  and fail closed if that isolation cannot be confirmed. Starting GPU Chatterbox synthesis also
  unloads Project Master's warm Ollama runner after acquiring the shared GPU lease, and normal
  backend shutdown unloads the final active runner before a later relaunch.
- Added a truthful `synthetic_reference` voice-rights basis for generated reference audio and
  stopped substituting the reference WAV itself as consent or license evidence.
- Changed dashboard Customize actions to return to Command with the requested panel visible,
  wrapped compact-window header controls, and exposed the explicit trusted-host list required to
  save a remote ComfyUI profile.
- Changed the Fedora local package build to compile once, preserve the correct RPM/AppImage bundle
  markers, validate the fully deployed AppDir, bypass the obsolete linuxdeploy RELR strip failure,
  and produce one branded, versioned AppImage before checksum verification.
- Added a thinking-mode selection policy to the system prompt's communication rules: start at
  Medium, use the lowest level that fits the task, and reserve High/Maximum for work that genuinely
  benefits from deeper analysis instead of treating reasoning depth as a quality scale.
- The Linux desktop now disables WebKitGTK's DMA-BUF renderer at launch unless the user has
  explicitly set `WEBKIT_DISABLE_DMABUF_RENDERER` themselves. Hybrid Intel/NVIDIA Wayland systems
  otherwise render a blank window with repeated "Failed to create GBM buffer" errors.
- Changed the interactive-inference busy error to stop blaming the background Dream worker: a
  request that arrives while a just-cancelled run is still winding down now reports that local
  models are finishing the previous request and suggests retrying, and the lease wait grew from
  10 to 15 seconds so quick resends after Stop usually succeed instead of erroring.
- Fixed Linux desktop quit orphaning the packaged backend: the PyInstaller sidecar forks the real
  server process, and killing only the launcher left that server holding port 8765 with the old
  session token, which would have blocked the next launch. Quit now collects the sidecar's
  descendant processes, stops the launcher, and terminates the descendants so uvicorn shuts down
  cleanly and releases the port. Discovered during Mike's supervised GUI pass.
- Fixed unreadable drop-down menus on Linux: WebKitGTK draws `<select>` popups on a native light
  background, so inherited near-white option text was invisible. Options are now black on white
  with grey disabled entries.

### Security

- Chat tools are read-only by default. Every mutating tool is hidden from model schemas and rejected
  at execution unless Mike explicitly enables **Allow project changes**. The desktop toggle
  applies only to the current live conversation and resets on app launch or conversation change;
  authorization is independently scoped to each backend request and works the same way in Direct,
  Team, sync, and streaming paths.
- Consecutive duplicate tool calls are suppressed before they can repeat a side effect. Project
  Master then requests one tool-free final response and preserves a truthful partial result if the
  model still cannot finish.
- Workspace file tools remain inside the active project root. Linux terminal calls accept argument
  arrays rather than shell strings, enforce time/resource/output bounds, use Bubblewrap for
  project-bound execution when available, and keep network access separately disabled by default.
  The no-Bubblewrap fallback is an explicitly read-only command allowlist rather than a filesystem
  sandbox.
- Dream outputs remain proposals and never promote themselves. Product planning proposals remain
  separate from runtime Approval Center decisions.
- ComfyUI defaults to loopback, requires explicit HTTPS host trust for remote profiles, refuses
  cross-origin redirects, executes only approved workflow digests, and validates filenames, paths,
  sizes, MIME types, and hashes before persisting outputs.
- Voice references stay in app-owned storage, require an explicit rights attestation before profile
  use, and produce checksum-verified artifacts. Synthetic references remain explicitly distinct
  from real-person voices, and consent/license modes require separate evidence. Normal app startup
  does not execute voice-engine installers or download model assets.

### Known issues

- Ollama and at least one compatible conversational model are still required for chat and Dream.
  Local model quality determines tool-call reliability.
- This machine does not have the configured `qwen3:8b` default. The first GUI pass must select an
  installed model; the release process must not silently download that model.
- Team mode is intentionally sequential and can take longer when many compatible models are
  installed. This protects machines with limited VRAM but is not low-latency fan-out.
- Project Master unloads only runners used by its own shared Ollama clients. Models loaded by a
  different local application remain outside that ownership boundary and can still compete for RAM.
- GPT-OSS does not support disabling thinking; Project Master requests its lowest supported level.
  Full per-model thinking-mode discovery and user controls remain a post-v0.3 follow-up.
- The desktop enables self-voice and synthetic/generated reference classifications. Explicit
  consent and licensed-voice records need a future evidence-document upload and verification
  registry; those choices remain disabled in the desktop rather than treating an unchecked
  identifier as verified evidence.
- Scheduled Dream work runs only while the application/backend is running. Project sources require
  explicit consent, an indexed Binder, and recipe scopes; memory sources remain an advanced
  metadata-driven path.
- ComfyUI and Chatterbox are optional, separately installed integrations. ComfyUI being unavailable
  must not block application startup; Chatterbox setup is a large explicit download and currently
  targets the pinned Linux/PyTorch stack documented in `docs/LINUX_PACKAGING.md`.
- Project Binder uses local lexical SQLite search rather than embeddings or a vector database.
- eSpeak NG is a reliable lightweight fallback, not neural voice cloning.
- The source engine directory retains the legacy name `ProjectMaster-v0.1.0`; package and runtime
  versions are nevertheless `0.3.0`.
- The signed beta updater channel and beta publishing automation are not yet provisioned. Install
  and upgrade this candidate manually until version-matched signed update artifacts exist.
- `RC` is currently a display label while every package already uses plain semantic version
  `0.3.0`. The public release must therefore ship the exact accepted candidate bits or advance the
  package version; a different same-version final would not be a valid updater upgrade.

### Verification

- The portable packaging test and Fedora prerequisite preflight passed with every package, Python,
  Cargo, Tauri, and runtime version source aligned at `0.3.0`.
- All five updater-policy tests passed, Cargo metadata reported `master 0.3.0`, the Python package
  imported as `0.3.0`, and the release-metadata diff passed whitespace validation.
- The complete Python suite passed with 264 tests and one intentional skip; Ruff passed. The
  frontend passed all 31 tests and its production TypeScript/Vite build (299 modules).
- Rust formatting, strict Clippy, and all five native lifecycle/version tests passed.
- The frozen 0.3.0 Python sidecar passed authenticated startup, unauthenticated rejection,
  Ollama-offline truthfulness, tool diagnostics, database creation, shutdown, and port-release
  checks. Its PyInstaller archive contains the packaged Chatterbox worker resource.
- The exact unsigned local AppImage and RPM passed SHA-256 verification. The AppImage hash is
  `07dfbcb5d6431648c248f6e785c8909be257bb9473c64ac4610baff9c1552012`; the RPM hash is
  `055db1a24860a558b88f77e73ecb80149a75f61f8d4416b689cd7e24b7b02128`.
- The exact AppDir backend passed the reproducible headless Fedora gate in 463,898 ms with the GUI
  closed: all 27 checks passed, all 13 Ollama tags were accounted for as 12 physical models, the 26B
  SuperGemma completed in 28,325 ms, both voice engines rendered checksum-verified WAVs, and final
  cleanup left no Ollama or backend process resident. The RPM remains uninstalled and Mike's short
  GUI daily-driver pass is still required before public release.
- Mike explicitly authorized shipping on 2026-07-27 after his supervised GUI pass surfaced and
  verified fixes on the final candidate. The v0.3.0 tag and GitHub pre-release were published with
  the accepted AppImage, unsigned RPM, and SHA-256 manifest. GitHub renames the RPM asset's space
  to a period (`Project.Master-0.3.0-1.x86_64.rpm`); its hash matches the manifest entry.

## 0.2.2 ALPHA — 2026-07-15

### Added

- Added signed in-app desktop updates with an explicit user confirmation before download, install,
  and restart.
- Added a daily update-check policy for alpha builds and a weekly policy for beta and stable builds.
- Added an automated prerelease workflow that signs Windows updater artifacts and maintains a
  rolling GitHub alpha update channel.

### Changed

- Existing v0.2.1 users must manually install v0.2.2 once to receive the updater. Beginning with
  this release, future updates can be discovered and installed from within Project Master.

### Security

- Updater packages are signed with a dedicated password-protected Tauri key and verified against
  the public key embedded in the desktop application before installation.

### Known issues

- Ollama and at least one compatible local model are still required.
- The Windows installer is updater-signed but not Authenticode-signed, so Microsoft reputation
  warnings can still appear during the initial manual installation.
- Users on v0.2.1 or earlier cannot receive this release automatically and must install it manually
  from GitHub once.

### Verification

- Backend Ruff checks passed and all 41 backend tests passed.
- All 17 frontend tests, the production frontend build, and the high-severity npm audit passed with
  zero reported vulnerabilities.
- Rust formatting, strict Clippy checks, and all five native lifecycle/version tests passed.
- The packaged v0.2.2 backend sidecar passed its health smoke test.
- The exact NSIS and MSI installers built successfully with matching Tauri updater signatures and
  SHA-256 checksums.
- The NSIS installer upgraded the local installation in place; the installed v0.2.2 application
  auto-started and cleanly stopped its packaged backend.
- A live installed-app test through Ollama and `qwen3:8b` returned exactly `OLLAMA_V022_OK`.

## 0.2.1 ALPHA — 2026-07-15

### Changed

- Added a conservative communication-fidelity foundation to the Python engine. It now preserves
  literal user text alongside labeled intent, ambiguity, prior context, and response-planning data
  before a model responds.
- Replaced the profile's absence-based style assumptions with an auditable communication profile for
  explicit preferences, corrections, disliked response patterns, source, confidence, examples,
  scope, timestamps, and superseded records.
- Added local communication-profile inspection and explicit feedback endpoints so a future interface
  can show the profile and record a deliberate correction without converting it into a factual memory.
- Added a local **Communication** tab to the desktop Customize panel. It shows active interaction
  rules and records scoped corrections such as changed meaning, assumptions, unwanted advice,
  repetition, or ignored context.
- Added context-aware response checks for unsupported user attributions, reintroduced corrections,
  contradictions with an existing project, unsolicited advice, unnecessary repetition, tone-based
  invalidation, belief mirroring, and inference presented as fact. Non-streaming replies receive one
  bounded repair attempt for material semantic-fidelity findings.

### Verification

- Focused backend communication, profile, audit, prompt, memory-authorization, and API tests passed.
- Backend Ruff checks passed.

## 0.2.0 ALPHA — 2026-07-14

### Added

- Added the Conversation Library: create a clean session, reopen saved conversations, and review
  their locally stored message history from a persistent workspace sidebar.
- Added a grounded first-session intake and a literal capability contract for Project Master.

### Changed

- Replaced generic, overly theatrical assistant behavior with a calmer default voice. Unprompted
  emojis, hype, glitter, magic, space, aliens, and whimsical roleplay are now discouraged.

### Verification

- Frontend conversation protocol tests, prompt-contract tests, memory-authorization tests, and the
  frontend production build passed before packaging.

## Release gate

No build may be uploaded or published unless all of the following are complete:

- Add a versioned entry to this changelog.
- Draft version-matched GitHub Release notes describing changes, fixes, known issues, and
  verification.
- Build and test the exact artifacts intended for upload.
- Generate checksums after the final artifacts are produced.
- Confirm the changelog, release notes, artifact filenames, application version, tag, and checksums
  all use the same version.

If any item is missing, the release is blocked. Finish the release documentation before uploading
another build.

`CHANGELOG.md` is the only rolling changelog kept on the default branch. Do not add separate
per-version changelog or release-note files to the default branch. Version-specific notes belong in
the matching GitHub Release; historical tags preserve the repository exactly as it existed for that
release.

## 0.1.3 ALPHA — 2026-07-14

### Added

- Added a versioned, declarative layout schema with validated operations for panel visibility,
  collapsed state, ordering, tab grouping, and constrained widths.
- Added an interface customization panel with chat and panel sizing, collapse/expand behavior,
  named saved layouts, local persistence, Undo, and Reset to default.
- Added executable break-tests for invalid panels, arbitrary operations, forbidden states, stale
  revisions, invalid dimensions, oversized batches, transactional rollback, and corrupt storage.

### Fixed

- Stop now sends an explicit cancellation request to the Python engine, which closes the active
  Ollama response and releases the provider before another prompt begins.
- Durable memory can no longer be created from ordinary or exploratory chat by a model tool call;
  a user must explicitly ask Project Master to remember, save, or store the information.
- The desktop app now rejects a mismatched backend already using its local port instead of silently
  attaching a newer interface to an older backend and failing chat requests.

### Repository maintenance

- Removed obsolete build handoffs, portable AI prompts, duplicate release-note files, and stale task
  tracking documents from the default branch.
- Consolidated release history into this rolling changelog.

### Known issues

- Ollama and at least one compatible local model are still required.
- Windows installers are not code-signed and may trigger a Microsoft reputation warning.
- Tool, memory, evidence, settings, and conversation-management interfaces are not built yet.

### Verification

- Targeted memory-authorization tests and layout/client protocol tests passed before the release build.
- Frontend production build, packaged backend build, and Windows NSIS/MSI installer builds passed.

## 0.1.1 ALPHA — 2026-07-14

### Changed

- Packaged the existing Python AI engine with the Tauri desktop installers.
- Added desktop-managed backend startup, readiness checks, application-data paths, logging, and
  shutdown.
- Routed connection and conversation Retry actions through the managed backend lifecycle.

### Fixed

- Fixed the v0.1.0 installer opening with the backend permanently offline.
- Added stale-backend replacement and recovery after a backend crash.
- Ensured the complete backend process tree stops when Project Master exits.

### Known issues

- Ollama and at least one compatible local model are still required.
- Windows installers are not code-signed and may trigger a Microsoft reputation warning.
- Tool, memory, evidence, settings, and conversation-management interfaces are not built yet.

### Verification

- Python tests and lint passed.
- Frontend production build passed.
- Rust lifecycle tests, formatting, and lint passed.
- NSIS and MSI installer builds passed.
- Installed backend startup, shutdown, crash detection, and one-click recovery passed.

## 0.1.0 ALPHA — 2026-07-14

### Added

- Released the first public Project Master desktop alpha.
- Connected the React and Tauri interface to the Python AI engine.
- Added streaming chat, model selection, cancellation, conversation persistence, memory, tools,
  evidence tracking, adaptive communication style, and response auditing.

### Known issues

- The Windows installer did not bundle or start the Python backend, so a normal installation opened
  in an offline state. This was corrected in 0.1.1 ALPHA.
