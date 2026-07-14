# Repository Contribution Rules

Before editing the Python engine, read `constitution/CONSTITUTION.md`, `ROADMAP.md`, and the
documentation and tests for the subsystem being changed.

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
- Record user-visible changes in the repository's root `../CHANGELOG.md`.
- Keep one rolling changelog on the default branch. Put version-specific notes in the matching
  GitHub Release instead of adding separate release-note files.

## Current priority

Phase 2 starts with the Research Engine. Do not begin multi-agent autonomy before source provenance, claim decomposition, and completion verification are stronger.
