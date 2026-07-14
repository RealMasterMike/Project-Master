# Instructions for AI Coding Agents

Read these files before editing:

1. `constitution/CONSTITUTION.md`
2. `PROJECT_STATUS.md`
3. `ROADMAP.md`
4. the task file for the subsystem being changed

## Working rules

- Preserve the distinction between memory, claims, evidence, and verified facts.
- Do not add unrestricted shell execution to the default tool set.
- Do not claim a feature is complete without tests or observable completion evidence.
- Prefer small, testable changes over broad rewrites.
- Keep the core provider-agnostic even when implementing provider-specific features.
- Adaptive personality may change presentation, never factual standards.
- Human-like emotional phrasing must not imply literal machine feelings or consciousness.
- Keep Windows as a first-class supported environment.
- Run `pytest` after code changes. Run `ruff check .` when Ruff is installed.
- Update `CHANGELOG.md`, `PROJECT_STATUS.md`, and the relevant task file when a milestone changes.

## Current priority

Phase 2 starts with the Research Engine. Do not begin multi-agent autonomy before source provenance, claim decomposition, and completion verification are stronger.

## Portable handoff prompt

When another AI continues this project, tell it:

> Continue Project Master incrementally. First inspect the constitution, current status, roadmap, and relevant task file. Preserve provenance and calibrated confidence. Do not replace the existing architecture without identifying a concrete failure and migration plan.
