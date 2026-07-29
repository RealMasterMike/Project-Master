# Local Desktop API

Project Master exposes a loopback-only HTTP API for the Tauri desktop client. The API is an
adapter over the same runtime used by the CLI; it does not replace the agent, tools, memory,
evidence ledger, personality profile, or response auditor.

## Run for development

From `C:\Master\ProjectMaster-v0.1.0`:

```powershell
.\.venv\Scripts\Activate.ps1
master serve
```

The default address is `http://127.0.0.1:8765`. Keep this terminal open when testing the API as a
standalone development service. The installed desktop app packages the same runtime as a sidecar
and manages startup, recovery, and shutdown automatically. The service intentionally binds to the
local machine only.

When `MASTER_SESSION_TOKEN` is set, every non-preflight request must send the same value in the
`X-Project-Master-Token` header. The packaged desktop generates and supplies this token
automatically.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/health` | Service and Ollama health |
| `GET` | `/api/v1/models/status` | Configured and recommended conversational models, installed catalog, and context length |
| `POST` | `/api/v1/chat` | Complete agent response with tool activity and audit findings |
| `POST` | `/api/v1/chat/stream` | NDJSON token, tool, completion, and error events |
| `POST` | `/api/v1/chat/cancel` | Cancel an active stream by request ID |
| `POST` | `/api/v1/conversations` | Create a persistent conversation |
| `GET` | `/api/v1/conversations` | List persistent conversations |
| `GET` | `/api/v1/conversations/{id}` | Read conversation messages |
| `GET` | `/api/v1/projects` | List projects, optionally filtered by `project_type` |
| `POST` | `/api/v1/projects` | Create a general or Creator project |
| `GET` | `/api/v1/media/health` | Media limits, integrity policy, and FFmpeg/FFprobe availability |
| `GET` | `/api/v1/projects/{project_id}/media` | List one project's media assets |
| `POST` | `/api/v1/projects/{project_id}/media` | Import a raw image, video, or audio body |
| `POST` | `/api/v1/projects/{project_id}/media/{asset_id}/trim` | Create a derived MP4 from an exact time range |
| `GET` | `/api/v1/media/assets/{asset_id}/content` | Read a SHA-256-verified media asset |
| `GET` | `/api/v1/integrations/comfyui` | List configured profiles, workflow revisions, and jobs |
| `POST` | `/api/v1/integrations/comfyui/workflows` | Import an immutable API-format workflow revision |
| `GET` | `/api/v1/integrations/comfyui/workflows/{revision_id}/compatibility/{profile_id}` | Preflight required node types and audited fixed model resources |
| `POST` | `/api/v1/integrations/comfyui/jobs` | Queue an approved, compatible workflow revision |
| `POST` | `/api/v1/integrations/comfyui/jobs/{job_id}/refresh` | Reconcile a job and catalog verified outputs |

Chat requests accept `message`, optional `conversation_id`, optional `model`, optional
`project_id`, up to three optional `image_asset_ids`, and an optional `request_id`. Image
attachments require Direct mode, a Creator project, project-owned verified image assets, and an
installed vision-capable model. Each image is limited to 20 MiB and the request total to 40 MiB.
The backend verifies dimensions, size, and SHA-256 before placing base64 bytes only on the current
transient Ollama user message; persisted conversations and run records remain text-only.
The desktop's automatic choice is limited to the exact physically verified
`lukey03/qwen3.5-9b-abliterated-vision:latest` tag and Ollama manifest digest when installed.
Another vision-capable tag can be selected explicitly, but remains manual/unverified and is never
an automatic fallback.

Mutating tools require `allow_mutations: true`. `web_search` and `web_fetch` are independently
omitted and blocked unless the request sets `allow_web_search: true`. `web_fetch` can read bounded
text directly from validated public HTTP(S) pages and treats it as untrusted external data.
`web_search` additionally requires `MASTER_SEARXNG_URL` to point to a configured SearXNG service
and requests `safesearch=0`. The permission is request-scoped and the desktop resets its control
when a conversation is opened or created.

The desktop client assigns a unique request ID to every stream. Sending that ID to
`/api/v1/chat/cancel` cooperatively stops the agent and closes the active Ollama response, including
when the desktop fetch has already disconnected. A cancellation requested just before stream
registration is retained briefly to prevent a start/cancel race.

When Direct chat omits `model`, the API refreshes the installed catalog and resolves only an exact
curated tag-plus-manifest identity. An image-bearing request additionally requires the curated
`vision` purpose and reported vision capability. `MASTER_MODEL` is honored as the preferred
automatic choice only when that same identity check passes. Sending a model explicitly remains a
deliberate manual/unverified choice. The engine remains responsible for system prompting, tools,
memory, evidence, personality adaptation, and persistence.

Each physical entry returned by `/api/v1/models/status` includes its manifest `digest`,
`automatic_eligible`, and `curated_purposes`. Automatic Direct recommendations and Team/Dream role
assignment require an exact curated tag plus the tested digest and the matching `chat`, `team`, or
`dream` purpose. The catalog is refreshed at execution admission. Unknown or changed identities
are reported but remain manual/unverified; neither a reported capability nor words such as
`uncensored` or `abliterated` in a tag establish provenance.

## Creator projects

Create a Creator project with:

```json
{
  "name": "Channel launch",
  "description": "Ideas and production media for the launch",
  "project_type": "creator",
  "metadata": {}
}
```

Send this body to `POST /api/v1/projects`. `project_type` accepts `general` or `creator` and
defaults to `general` for existing clients. `GET /api/v1/projects?project_type=creator` returns only
Creator projects. The Creator Ideas interface uses the existing durable Dream API with
project-specific source and request identifiers; its Creator Spark output stays in the
proposal-only inbox until a human passes on it or promotes it to a media-brief candidate.

## Project media and video trimming

`POST /api/v1/projects/{project_id}/media` accepts the file itself as the request body, not
multipart form data. Supply its base file name through the `file_name` query parameter or the
`X-File-Name` header and its media type through `Content-Type`. Imports are bounded by the limit
reported by `GET /api/v1/media/health`; the current default is 512 MiB. Project Master validates
the file signature before placing the content in private, content-addressed storage.

Every returned media asset includes its `id`, linked `project_ids`, `name`, `kind`, `source`,
`media_type`, `sha256`, `size_bytes`, optional duration and dimensions, creation time, and optional
`derivation`. Reads through `GET /api/v1/media/assets/{asset_id}/content` verify the stored size and
SHA-256 before returning private inline content.

To trim a project video without modifying its source, send:

```json
{
  "start_seconds": 12.25,
  "end_seconds": 47.75,
  "output_name": "opening-cut.mp4"
}
```

to:

```text
POST /api/v1/projects/{project_id}/media/{asset_id}/trim
```

Bounds must be finite, start at or after zero, end after start, and not exceed a known source
duration. `output_name` is optional but, when supplied, must be a base file name ending in `.mp4`.
The operation creates a separate H.264 video/AAC audio MP4, preserves the original asset, and
returns the new asset under `asset`. The new record uses `source: "trim"` and includes:

```json
{
  "derivation": {
    "operation": "video_trim",
    "source_asset_id": "media-asset-...",
    "start_seconds": 12.25,
    "end_seconds": 47.75,
    "recipe": "mp4-h264-aac-v1"
  }
}
```

Only one trim runs at a time, FFmpeg execution is bounded, and a request fails closed if the source
does not pass its stored integrity check. FFmpeg availability is reported by the media health
endpoint.

## ComfyUI workflow purpose and compatibility

Workflow import accepts an API-format ComfyUI workflow, optional typed bindings, and an optional
`purpose` classification:

```json
{
  "name": "Creator video workflow",
  "purpose": "video",
  "workflow": {
    "1": {
      "class_type": "ExampleNode",
      "inputs": {}
    }
  },
  "bindings": []
}
```

`purpose` accepts `general`, `image`, `video`, or `audio`, defaults to `general`, and is included in
the immutable workflow digest. Bindings may include the narrow `image_asset` type only for
`LoadImage.image`. At submission, Project Master resolves that value as an asset in the selected
project, verifies its stored metadata and content, uploads a sanitized copy into ComfyUI's fixed
`project-master` input namespace, and records durable source-asset provenance without persisting a
host filesystem path.

The compatibility endpoint compares every workflow `class_type` against the selected live
profile's ComfyUI object catalog. It returns `compatible`, `missing_node_types`, and structured
`missing_resources` records for audited fixed checkpoint, UNet, GGUF UNet, CLIP, GGUF CLIP, VAE,
and model-only LoRA loader inputs. Dynamic values and arbitrary third-party loaders are not
misrepresented as statically verified.

Queue requests take `profile_id`, `workflow_revision_id`, optional typed `values`, and optional
`project_id`. A revision must be approved. Queueing also repeats the live-node preflight: if the
profile cannot report its node catalog, no job is created or submitted; if requirements are
missing, the API returns `409` with both missing-node and missing-resource detail. Supplying
`project_id` associates the job and its input/output provenance with that project. Once outputs
have been fetched and verified, job refresh, reconciliation, and the ComfyUI overview catalog them
in the project's Media library with `source: "comfyui"`.

The runtime also performs a local GPU handoff around these API operations. Before an interactive
Ollama request starts, Project Master waits for every reachable configured ComfyUI queue to become
idle, then sends ComfyUI's official `POST /free` request with `unload_models` and `free_memory`
enabled. A running or pending ComfyUI prompt keeps the request on the existing local-model busy
path; an unreachable optional profile is skipped rather than making chat unavailable. Conversely,
before `POST /api/v1/integrations/comfyui/jobs` submits the remote prompt, Project Master unloads
only the Ollama runner tracked as owned by its current client family. Queue observations do not
establish ownership of work submitted by other ComfyUI clients.

Project Master seeds four audited workflow definitions covering text-to-image, image-to-image,
text-to-video, and image-to-video. Their automatic-default model stacks are limited to
publisher-documented uncensored or SFW+NSFW-capable releases. Project Master does not bundle or
silently download their multi-gigabyte weights; the selected ComfyUI installation must provide the
documented files before compatibility succeeds.

## Configuration

```env
MASTER_MODEL=hf.co/TrevorJS/gemma-4-E4B-it-uncensored-GGUF:Q4_K_M
MASTER_NUM_CTX=65536
```

The shipped `MASTER_NUM_CTX` default is `65536`. It is sent to Ollama as `options.num_ctx` for both
regular and streaming requests. Larger context windows increase memory use and can slow initial
model loading.
