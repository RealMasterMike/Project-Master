from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from project_master import __version__
from project_master.agent import ProjectMasterAgent
from project_master.config import MasterConfig
from project_master.core.audit import audit_response
from project_master.llm.ollama import OllamaClient, OllamaError
from project_master.memory.store import SQLiteStore
from project_master.personality.profile import StyleProfiler
from project_master.runtime import build_runtime

app = typer.Typer(
    name="master",
    help="Project Master: local-first epistemic AI framework.",
    no_args_is_help=True,
)
console = Console()


def _runtime(
    config_path: Path | None = None,
) -> tuple[
    MasterConfig,
    SQLiteStore,
    StyleProfiler,
    OllamaClient,
    ProjectMasterAgent,
]:
    runtime = build_runtime(config_path)
    return runtime.config, runtime.store, runtime.profiler, runtime.provider, runtime.agent


@app.command()
def version() -> None:
    """Show the installed Project Master version."""
    console.print(f"Project Master {__version__}")


@app.command()
def doctor(
    config_path: Annotated[
        Path | None, typer.Option("--config", help="Path to YAML config.")
    ] = None,
) -> None:
    """Check configuration, storage, workspace, Ollama, and the configured model."""
    config, store, _profiler, provider, _agent = _runtime(config_path)
    table = Table(title="Project Master Doctor")
    table.add_column("Check")
    table.add_column("Result")
    table.add_row("Version", __version__)
    table.add_row("Database", str(store.path.resolve()))
    table.add_row("Workspace", str(config.workspace_root.resolve()))
    table.add_row("File writes", "enabled" if config.allow_file_writes else "disabled")
    table.add_row("Configured model", config.model)
    try:
        health = provider.health()
        models = health["models"]
        model_present = config.model in models or any(
            name.split(":")[0] == config.model.split(":")[0] for name in models
        )
        table.add_row("Ollama", "reachable")
        table.add_row(
            "Model installed",
            "yes" if model_present else f"not detected; installed: {', '.join(models) or 'none'}",
        )
    except OllamaError as exc:
        table.add_row("Ollama", f"ERROR: {exc}")
    console.print(table)


@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="Question or instruction for Project Master.")],
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
    show_tools: Annotated[bool, typer.Option("--show-tools")] = False,
) -> None:
    """Ask one question in a temporary session."""
    _config, store, _profiler, _provider, agent = _runtime(config_path)
    session_id = store.create_session(title=question[:80])
    try:
        answer, executions = agent.respond(session_id, question)
    except OllamaError as exc:
        console.print(f"[red]Ollama error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(Markdown(answer))
    if show_tools and executions:
        _print_tool_executions(executions)


@app.command()
def chat(
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
    show_tools: Annotated[bool, typer.Option("--show-tools")] = False,
) -> None:
    """Start an interactive Project Master session."""
    config, store, profiler, provider, agent = _runtime(config_path)
    try:
        health = provider.health()
    except OllamaError as exc:
        console.print(f"[red]Ollama is unavailable:[/red] {exc}")
        console.print(
            "Start Ollama, confirm MASTER_OLLAMA_URL, then run [bold]master doctor[/bold]."
        )
        raise typer.Exit(code=1) from exc

    installed = health.get("models", [])
    if config.model not in installed and not any(
        name.split(":")[0] == config.model.split(":")[0] for name in installed
    ):
        console.print(
            f"[yellow]Warning:[/yellow] configured model {config.model!r} was not detected. "
            "Run `master doctor` or update .env."
        )

    session_id = store.create_session(title="Interactive chat")
    audit_enabled = False
    console.print(
        Panel(
            f"Project Master v{__version__}\nModel: {config.model}\n"
            "Type /help for commands. Project Master will not suggest ending "
            "the session based only on duration.",
            title="Foundation",
        )
    )

    while True:
        try:
            text = console.input("[bold cyan]you>[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nSession closed.")
            break
        if not text:
            continue
        if text.startswith("/"):
            handled, should_exit, audit_enabled = _handle_chat_command(
                text, store, profiler, audit_enabled
            )
            if should_exit:
                break
            if handled:
                continue
        try:
            answer, executions = agent.respond(session_id, text)
        except OllamaError as exc:
            console.print(f"[red]Ollama error:[/red] {exc}")
            continue
        console.print("[bold green]master>[/bold green]")
        console.print(Markdown(answer))
        if show_tools and executions:
            _print_tool_executions(executions)
        if audit_enabled:
            findings = audit_response(answer)
            if findings:
                _print_audit(findings)


@app.command("claims")
def list_claims(
    status: Annotated[str | None, typer.Option("--status")] = None,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """List claims and attached evidence."""
    _config, store, _profiler, _provider, _agent = _runtime(config_path)
    _print_claims(store.list_claims(status=status))


@app.command("memories")
def list_memories(
    query: Annotated[str, typer.Option("--query", "-q")] = "",
    namespace: Annotated[str | None, typer.Option("--namespace", "-n")] = None,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """List stored memories with provenance and confidence."""
    _config, store, _profiler, _provider, _agent = _runtime(config_path)
    rows = store.recall(query=query, namespace=namespace, limit=100)
    table = Table(title="Memory")
    table.add_column("Namespace")
    table.add_column("Key")
    table.add_column("Value")
    table.add_column("Source")
    table.add_column("Confidence")
    for row in rows:
        table.add_row(
            row["namespace"],
            row["key"],
            json.dumps(row["value"], ensure_ascii=False),
            row["source"],
            f"{row['confidence']:.2f}",
        )
    console.print(table)


@app.command()
def audit(
    text: Annotated[str, typer.Argument(help="Text to audit for epistemic warning signs.")],
) -> None:
    """Run the deterministic response auditor."""
    findings = audit_response(text)
    if not findings:
        console.print("No heuristic findings.")
        return
    _print_audit(findings)


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Local interface to bind.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Local API port.")] = 8765,
) -> None:
    """Run the local API used by the Project Master desktop client."""
    import uvicorn

    uvicorn.run("project_master.api:app", host=host, port=port, reload=False)


def _handle_chat_command(
    text: str,
    store: SQLiteStore,
    profiler: StyleProfiler,
    audit_enabled: bool,
) -> tuple[bool, bool, bool]:
    command, *rest = text.split(maxsplit=1)
    argument = rest[0].strip() if rest else ""
    if command in {"/quit", "/exit"}:
        console.print("Session closed.")
        return True, True, audit_enabled
    if command == "/help":
        console.print(
            "/help — show commands\n"
            "/profile — show adaptive communication profile\n"
            "/claims — show evidence ledger\n"
            "/memories — show stored context\n"
            "/audit on|off — toggle response linting\n"
            "/quit — close the session"
        )
        return True, False, audit_enabled
    if command == "/profile":
        console.print_json(data=profiler.profile.to_dict())
        return True, False, audit_enabled
    if command == "/claims":
        _print_claims(store.list_claims())
        return True, False, audit_enabled
    if command == "/memories":
        rows = store.recall(limit=30)
        console.print_json(data=rows)
        return True, False, audit_enabled
    if command == "/audit":
        if argument.lower() not in {"on", "off"}:
            console.print("Usage: /audit on|off")
            return True, False, audit_enabled
        audit_enabled = argument.lower() == "on"
        console.print(f"Response audit {'enabled' if audit_enabled else 'disabled'}.")
        return True, False, audit_enabled
    return False, False, audit_enabled


def _print_claims(claims: list[dict[str, object]]) -> None:
    if not claims:
        console.print("No claims recorded.")
        return
    for claim in claims:
        evidence = claim.get("evidence", [])
        console.print(
            Panel(
                f"Status: {claim['status']}\nConfidence: {float(claim['confidence']):.2f}\n"
                f"Assessment: {claim.get('assessment') or '(none)'}\n"
                f"Evidence items: {len(evidence) if isinstance(evidence, list) else 0}",
                title=f"Claim {claim['id']}: {claim['statement']}",
            )
        )
        if isinstance(evidence, list):
            for item in evidence:
                if isinstance(item, dict):
                    console.print(
                        f"  - {item.get('stance')}: {item.get('summary')} "
                        f"(reliability={float(item.get('reliability', 0)):.2f}, "
                        f"source={item.get('source_ref') or item.get('source_type')})"
                    )


def _print_tool_executions(executions: list[object]) -> None:
    table = Table(title="Tool activity")
    table.add_column("Tool")
    table.add_column("Status")
    table.add_column("Arguments")
    for execution in executions:
        table.add_row(
            execution.name,
            "ok" if execution.ok else "error",
            json.dumps(execution.arguments, ensure_ascii=False),
        )
    console.print(table)


def _print_audit(findings: list[object]) -> None:
    table = Table(title="Epistemic audit")
    table.add_column("Severity")
    table.add_column("Code")
    table.add_column("Finding")
    for finding in findings:
        table.add_row(
            finding.severity,
            finding.code,
            finding.message,
        )
    console.print(table)
