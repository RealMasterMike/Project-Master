# Memory

## Principle

Memory improves continuity but does not certify truth.

## Memory classes

- `user_preference`: stable interaction and workflow preferences.
- `project`: decisions, architecture, status, and constraints.
- `personal_context`: user-supplied context relevant to future assistance.
- `external_claim`: a claim about the world that still requires evidence.
- `temporary`: short-lived session context.

## Required metadata

Every persistent memory includes:

- namespace;
- key;
- JSON value;
- source;
- confidence;
- creation and update timestamps.

## Retrieval

v0.1 uses simple SQLite text matching. Future versions should add embeddings while preserving provenance and deletion controls.
