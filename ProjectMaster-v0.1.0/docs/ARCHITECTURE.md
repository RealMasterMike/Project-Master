# Architecture

## v0.1 runtime

```text
User
  ↓
CLI
  ↓
Adaptive style profiler ─────────────┐
  ↓                                  │
Prompt builder ← Constitution ← Memory
  ↓
Agent loop ↔ Ollama model
  ↓             ↕
Response       Tool registry
  ↓             ↕
Auditor      SQLite store / workspace
```

## Subsystems

### Constitution

Stable project-wide principles. It governs every model, prompt, tool, interface, and agent.

### Epistemic core

Builds instructions that distinguish facts, source claims, inferences, speculation, and conclusions. It also asks the model to communicate uncertainty and revision conditions.

### Model provider

`OllamaClient` implements local chat and native function calling over Ollama’s HTTP API.

### Agent loop

The agent sends messages and tool schemas to the model, executes requested tools, adds results to the conversation, and repeats until the model returns a final response or the configured tool-round limit is reached.

### Memory and evidence store

SQLite stores:

- persistent memories with namespace, source, and confidence;
- conversation sessions and messages;
- claims with status and confidence;
- evidence attached to claims with stance and reliability;
- adaptive communication profiles.

### Tool registry

Tools are explicit objects with a name, description, JSON parameter schema, and handler. v0.1 provides no unrestricted shell tool.

### Adaptive personality

A lightweight profile observes communication signals such as message length, profanity tolerance, and question density. It adjusts presentation, never factual conclusions.

### Response auditor

A deterministic linter flags possible overconfidence, unsupported universal language, and deceptive emotion claims. It is a warning mechanism, not a truth oracle.

## Extension points

- Add a provider by implementing the `ChatProvider` protocol.
- Add tools by registering `Tool` objects.
- Add search through a provider-specific plugin.
- Replace the heuristic style profiler with a transparent learned profile.
- Add a verifier model behind the same provider interface.
