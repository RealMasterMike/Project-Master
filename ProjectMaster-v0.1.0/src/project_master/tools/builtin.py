from __future__ import annotations

import ast
import operator
from datetime import datetime
from pathlib import Path
from typing import Any

from project_master.memory.store import SQLiteStore
from project_master.tools.base import Tool, ToolRegistry

_MAX_READ_BYTES = 1_000_000
_MAX_WRITE_BYTES = 1_000_000

_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPERATORS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def build_registry(
    store: SQLiteStore,
    workspace_root: Path,
    allow_file_writes: bool = False,
) -> ToolRegistry:
    registry = ToolRegistry()
    root = workspace_root.resolve()
    root.mkdir(parents=True, exist_ok=True)

    registry.register(
        Tool(
            name="calculator",
            description="Evaluate a basic arithmetic expression safely.",
            parameters={
                "type": "object",
                "properties": {"expression": {"type": "string"}},
                "required": ["expression"],
                "additionalProperties": False,
            },
            handler=lambda args: {"result": safe_calculate(str(args["expression"]))},
        )
    )

    registry.register(
        Tool(
            name="current_time",
            description="Return the local machine date and time in ISO 8601 format.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=lambda _args: {"local_time": datetime.now().astimezone().isoformat()},
        )
    )

    def workspace_list(args: dict[str, Any]) -> dict[str, Any]:
        target = _safe_path(root, str(args.get("path", ".")))
        if not target.exists():
            raise FileNotFoundError(target)
        if not target.is_dir():
            raise NotADirectoryError(target)
        limit = min(int(args.get("limit", 100)), 500)
        entries = []
        for item in sorted(
            target.iterdir(), key=lambda value: (not value.is_dir(), value.name.lower())
        )[:limit]:
            entries.append(
                {
                    "name": item.name,
                    "relative_path": str(item.relative_to(root)),
                    "type": "directory" if item.is_dir() else "file",
                    "size": item.stat().st_size if item.is_file() else None,
                }
            )
        return {"path": str(target.relative_to(root)), "entries": entries}

    registry.register(
        Tool(
            name="workspace_list",
            description=(
                "List files and directories inside the configured Project Master workspace."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": "."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
                },
                "additionalProperties": False,
            },
            handler=workspace_list,
        )
    )

    def workspace_read(args: dict[str, Any]) -> dict[str, Any]:
        target = _safe_path(root, str(args["path"]))
        if not target.is_file():
            raise FileNotFoundError(target)
        size = target.stat().st_size
        if size > _MAX_READ_BYTES:
            raise ValueError(f"File is too large to read ({size} bytes; maximum {_MAX_READ_BYTES})")
        return {
            "path": str(target.relative_to(root)),
            "content": target.read_text(encoding="utf-8"),
        }

    registry.register(
        Tool(
            name="workspace_read",
            description="Read a UTF-8 text file inside the configured workspace.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=workspace_read,
        )
    )

    def workspace_write(args: dict[str, Any]) -> dict[str, Any]:
        if not allow_file_writes:
            raise PermissionError(
                "Workspace writes are disabled. Set MASTER_ALLOW_FILE_WRITES=true to enable them."
            )
        content = str(args["content"])
        encoded = content.encode("utf-8")
        if len(encoded) > _MAX_WRITE_BYTES:
            raise ValueError(f"Content exceeds maximum write size of {_MAX_WRITE_BYTES} bytes")
        target = _safe_path(root, str(args["path"]))
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = str(args.get("mode", "overwrite"))
        if mode == "append":
            with target.open("a", encoding="utf-8") as handle:
                handle.write(content)
        elif mode == "overwrite":
            target.write_text(content, encoding="utf-8")
        else:
            raise ValueError("mode must be 'overwrite' or 'append'")
        return {"path": str(target.relative_to(root)), "bytes_written": len(encoded), "mode": mode}

    registry.register(
        Tool(
            name="workspace_write",
            description=(
                "Write a UTF-8 text file inside the configured workspace when writes are enabled."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "mode": {
                        "type": "string",
                        "enum": ["overwrite", "append"],
                        "default": "overwrite",
                    },
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            handler=workspace_write,
        )
    )

    def memory_remember(args: dict[str, Any]) -> dict[str, Any]:
        namespace = str(args.get("namespace", "project"))
        confidence = float(args.get("confidence", 0.8))
        _validate_unit_interval(confidence, "confidence")
        store.remember(
            namespace=namespace,
            key=str(args["key"]),
            value=args["value"],
            source=str(args.get("source", "assistant_tool")),
            confidence=confidence,
        )
        return {"stored": True, "namespace": namespace, "key": str(args["key"])}

    registry.register(
        Tool(
            name="memory_remember",
            description=(
                "Store durable context with provenance. Use for preferences or project decisions; "
                "do not treat memory as proof of an external claim."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "namespace": {
                        "type": "string",
                        "enum": [
                            "user_preference",
                            "project",
                            "personal_context",
                            "external_claim",
                            "temporary",
                        ],
                        "default": "project",
                    },
                    "key": {"type": "string"},
                    "value": {},
                    "source": {"type": "string", "default": "assistant_tool"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.8},
                },
                "required": ["key", "value"],
                "additionalProperties": False,
            },
            handler=memory_remember,
        )
    )

    registry.register(
        Tool(
            name="memory_recall",
            description="Search stored context by text and optional namespace.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "default": ""},
                    "namespace": {"type": ["string", "null"], "default": None},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                },
                "additionalProperties": False,
            },
            handler=lambda args: {
                "memories": store.recall(
                    query=str(args.get("query", "")),
                    namespace=args.get("namespace"),
                    limit=min(int(args.get("limit", 10)), 50),
                )
            },
        )
    )

    def claim_record(args: dict[str, Any]) -> dict[str, Any]:
        confidence = float(args.get("confidence", 0.0))
        _validate_unit_interval(confidence, "confidence")
        claim_id = store.record_claim(
            statement=str(args["statement"]),
            status=str(args.get("status", "unverified")),
            confidence=confidence,
            assessment=str(args.get("assessment", "")),
        )
        return {"claim_id": claim_id, "stored": True}

    registry.register(
        Tool(
            name="claim_record",
            description=(
                "Record a claim in the evidence ledger without automatically treating it as true."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "statement": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["unverified", "supported", "contradicted", "mixed", "unknown"],
                        "default": "unverified",
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1, "default": 0},
                    "assessment": {"type": "string", "default": ""},
                },
                "required": ["statement"],
                "additionalProperties": False,
            },
            handler=claim_record,
        )
    )

    def evidence_add(args: dict[str, Any]) -> dict[str, Any]:
        reliability = float(args.get("reliability", 0.5))
        _validate_unit_interval(reliability, "reliability")
        evidence_id = store.add_evidence(
            claim_id=int(args["claim_id"]),
            stance=str(args["stance"]),
            summary=str(args["summary"]),
            source_ref=str(args.get("source_ref", "")),
            source_type=str(args.get("source_type", "user_supplied")),
            reliability=reliability,
        )
        return {"evidence_id": evidence_id, "stored": True}

    registry.register(
        Tool(
            name="evidence_add",
            description=(
                "Attach supporting, contradicting, or contextual evidence to a recorded claim."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "claim_id": {"type": "integer"},
                    "stance": {"type": "string", "enum": ["supports", "contradicts", "context"]},
                    "summary": {"type": "string"},
                    "source_ref": {"type": "string", "default": ""},
                    "source_type": {"type": "string", "default": "user_supplied"},
                    "reliability": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5},
                },
                "required": ["claim_id", "stance", "summary"],
                "additionalProperties": False,
            },
            handler=evidence_add,
        )
    )

    registry.register(
        Tool(
            name="claims_list",
            description="List recorded claims and their evidence from the evidence ledger.",
            parameters={
                "type": "object",
                "properties": {
                    "status": {"type": ["string", "null"], "default": None},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                },
                "additionalProperties": False,
            },
            handler=lambda args: {
                "claims": store.list_claims(
                    status=args.get("status"),
                    limit=min(int(args.get("limit", 20)), 100),
                )
            },
        )
    )

    return registry


def safe_calculate(expression: str) -> int | float:
    if len(expression) > 200:
        raise ValueError("Expression is too long")
    node = ast.parse(expression, mode="eval")
    result = _eval_node(node.body)
    if isinstance(result, complex):
        raise ValueError("Complex results are not supported")
    if abs(float(result)) > 1e100:
        raise ValueError("Result is too large")
    return result


def _eval_node(node: ast.AST) -> int | float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        return _UNARY_OPERATORS[type(node.op)](_eval_node(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(float(right)) > 100:
            raise ValueError("Exponent is too large")
        return _BINARY_OPERATORS[type(node.op)](left, right)
    raise ValueError("Only basic arithmetic operators and numeric literals are allowed")


def _safe_path(root: Path, raw_path: str) -> Path:
    candidate = (root / raw_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PermissionError("Path escapes the configured workspace") from exc
    return candidate


def _validate_unit_interval(value: float, name: str) -> None:
    if not 0 <= value <= 1:
        raise ValueError(f"{name} must be between 0 and 1")
