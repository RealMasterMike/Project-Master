from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import asdict
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from project_master import __version__
from project_master.core.audit import audit_response
from project_master.llm.ollama import OllamaError
from project_master.runtime import MasterRuntime, build_runtime


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=100_000)
    conversation_id: str | None = None
    model: str | None = None


def create_app(runtime: MasterRuntime | None = None) -> FastAPI:
    active = runtime or build_runtime()
    app = FastAPI(title="Project Master Local API", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:1420", "tauri://localhost", "https://tauri.localhost"],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    def conversation_id(request: ChatRequest) -> str:
        if request.conversation_id:
            if not active.store.session_exists(request.conversation_id):
                raise HTTPException(status_code=404, detail="Conversation not found")
            return request.conversation_id
        return active.store.create_session(title=request.message[:80])

    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        try:
            ollama = active.provider.health()
        except OllamaError as exc:
            return {
                "ok": False,
                "service": "ready",
                "ollama": "unreachable",
                "error": str(exc),
                "version": __version__,
            }
        return {"ok": True, "service": "ready", "ollama": ollama, "version": __version__}

    @app.get("/api/v1/models/status")
    def model_status() -> dict[str, Any]:
        try:
            ollama = active.provider.health()
            models = ollama["models"]
            reachable = True
        except OllamaError:
            models = []
            reachable = False
        return {
            "configured_model": active.config.model,
            "num_ctx": active.config.num_ctx,
            "ollama_url": active.config.ollama_url,
            "ollama_reachable": reachable,
            "models": models,
        }

    @app.post("/api/v1/conversations", status_code=201)
    def create_conversation(body: ConversationCreate) -> dict[str, str]:
        return {"id": active.store.create_session(title=body.title)}

    @app.get("/api/v1/conversations")
    def list_conversations(limit: int = 50) -> dict[str, Any]:
        return {"conversations": active.store.list_sessions(limit=min(max(limit, 1), 200))}

    @app.get("/api/v1/conversations/{session_id}")
    def get_conversation(session_id: str) -> dict[str, Any]:
        if not active.store.session_exists(session_id):
            raise HTTPException(status_code=404, detail="Conversation not found")
        return {
            "id": session_id,
            "messages": active.store.recent_messages(session_id, limit=500),
        }

    @app.post("/api/v1/chat")
    def chat(body: ChatRequest) -> dict[str, Any]:
        session_id = conversation_id(body)
        try:
            answer, tools = active.agent_for_model(body.model).respond(session_id, body.message)
        except OllamaError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "conversation_id": session_id,
            "message": answer,
            "tools": [asdict(item) for item in tools],
            "audit": [asdict(item) for item in audit_response(answer)],
        }

    @app.post("/api/v1/chat/stream")
    def chat_stream(body: ChatRequest) -> StreamingResponse:
        session_id = conversation_id(body)

        def events() -> Iterator[str]:
            yield _event({"type": "start", "conversation_id": session_id})
            try:
                for event in active.agent_for_model(body.model).respond_stream(
                    session_id, body.message
                ):
                    if event["type"] == "done":
                        event["conversation_id"] = session_id
                        event["audit"] = [
                            asdict(item) for item in audit_response(str(event["content"]))
                        ]
                    yield _event(event)
            except OllamaError as exc:
                yield _event({"type": "error", "error": str(exc), "retryable": True})
            except Exception as exc:
                yield _event(
                    {
                        "type": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                        "retryable": False,
                    }
                )

        return StreamingResponse(events(), media_type="application/x-ndjson")

    return app


def _event(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str) + "\n"


app = create_app()
