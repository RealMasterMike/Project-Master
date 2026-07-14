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

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/health` | Service and Ollama health |
| `GET` | `/api/v1/models/status` | Configured model, installed models, and context length |
| `POST` | `/api/v1/chat` | Complete agent response with tool activity and audit findings |
| `POST` | `/api/v1/chat/stream` | NDJSON token, tool, completion, and error events |
| `POST` | `/api/v1/chat/cancel` | Cancel an active stream by request ID |
| `POST` | `/api/v1/conversations` | Create a persistent conversation |
| `GET` | `/api/v1/conversations` | List persistent conversations |
| `GET` | `/api/v1/conversations/{id}` | Read conversation messages |

Chat requests accept `message`, optional `conversation_id`, optional `model`, and an optional
`request_id`. The desktop client assigns a unique request ID to every stream. Sending that ID to
`/api/v1/chat/cancel` cooperatively stops the agent and closes the active Ollama response, including
when the desktop fetch has already disconnected. A cancellation requested just before stream
registration is retained briefly to prevent a start/cancel race.

When no model is sent, `MASTER_MODEL` is used. The engine remains responsible for system prompting,
tools, memory, evidence, personality adaptation, and persistence.

## Configuration

```env
MASTER_MODEL=qwen3:8b
MASTER_NUM_CTX=32768
```

`MASTER_NUM_CTX` is sent to Ollama as `options.num_ctx` for both regular and streaming requests.
Increasing context length increases memory use and can slow initial model loading.
