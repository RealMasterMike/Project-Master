from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from project_master.agent import ProjectMasterAgent
from project_master.config import MasterConfig
from project_master.core.prompting import PromptBuilder
from project_master.llm.ollama import OllamaClient
from project_master.memory.store import SQLiteStore
from project_master.personality.profile import StyleProfiler
from project_master.tools.builtin import build_registry


@dataclass(slots=True)
class MasterRuntime:
    config: MasterConfig
    store: SQLiteStore
    profiler: StyleProfiler
    provider: OllamaClient
    agent: ProjectMasterAgent

    def agent_for_model(self, model: str | None = None) -> ProjectMasterAgent:
        if not model or model == self.config.model:
            return self.agent
        provider = OllamaClient(
            base_url=self.config.ollama_url,
            model=model,
            temperature=self.config.temperature,
            num_ctx=self.config.num_ctx,
            timeout_seconds=self.config.request_timeout_seconds,
        )
        return ProjectMasterAgent(
            provider=provider,
            tools=self.agent.tools,
            store=self.store,
            profiler=self.profiler,
            prompt_builder=PromptBuilder(),
            max_tool_rounds=self.config.max_tool_rounds,
            max_history_messages=self.config.max_history_messages,
        )


def build_runtime(config_path: str | Path | None = None) -> MasterRuntime:
    config = MasterConfig.load(config_path)
    store = SQLiteStore(config.db_path)
    profiler = StyleProfiler(store)
    provider = OllamaClient(
        base_url=config.ollama_url,
        model=config.model,
        temperature=config.temperature,
        num_ctx=config.num_ctx,
        timeout_seconds=config.request_timeout_seconds,
    )
    tools = build_registry(store, config.workspace_root, config.allow_file_writes)
    agent = ProjectMasterAgent(
        provider=provider,
        tools=tools,
        store=store,
        profiler=profiler,
        prompt_builder=PromptBuilder(),
        max_tool_rounds=config.max_tool_rounds,
        max_history_messages=config.max_history_messages,
    )
    return MasterRuntime(config, store, profiler, provider, agent)
