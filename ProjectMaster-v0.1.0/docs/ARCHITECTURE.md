# Architecture

## Runtime

```text
User
  ↓
Conversation interpretation ──→ literal text, context, ambiguities, response plan
  ↓
Communication profile ───────────────┐
  ↓                                  │
Prompt builder ← Constitution ← Memory
  ↓
Agent loop ↔ Ollama model
  ↓             ↕
Fidelity guard  Tool registry
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

Before generation, the agent interprets the new turn against prior conversation history. The
interpretation preserves the literal text and labels its own intent and contextual inferences as
non-factual operational metadata. The agent then sends messages and tool schemas to the model,
executes requested tools, adds results to the conversation, and repeats until the model returns a
final response or the configured tool-round limit is reached.

### Memory and evidence store

SQLite stores:

- persistent memories with namespace, source, and confidence;
- conversation sessions and messages;
- claims with status and confidence;
- evidence attached to claims with stance and reliability;
- adaptive communication profiles.

### Tool registry

Tools are explicit objects with a name, description, JSON parameter schema, and handler. v0.1 provides no unrestricted shell tool.

### Communication profile

The profile stores explicit preferences, carefully bounded inferred presentation signals,
corrections, disliked response patterns, scope, timestamps, examples, confidence, and superseded
preferences. It does not store beliefs or infer approval from silence. User corrections are
evidence about communication behavior, not evidence about external facts.

### Conversation interpretation and response plan

The interpretation layer exposes: literal user text, message intent, likely handling, ambiguities,
relevant prior context, rejected interpretations, and a response plan. It is deliberately
conservative: inferred content remains labeled inference and cannot be presented as an explicit user
statement.

### Response auditor

A deterministic linter flags possible overconfidence, unsupported universal language, deceptive
emotion claims, unsupported user attributions, unsolicited advice, unnecessary repetition,
tone-based invalidation, reintroduced corrections, and certain contradictions with established
project context. Non-streaming responses receive one bounded repair pass when a high-confidence
semantic-fidelity failure is detected. Streaming preserves live token delivery, so it currently uses
the pre-generation interpretation but cannot retract already-emitted tokens; a buffered strict mode
would be required for pre-display streaming repair.

## Extension points

- Add a provider by implementing the `ChatProvider` protocol.
- Add tools by registering `Tool` objects.
- Add search through a provider-specific plugin.
- Replace the heuristic style profiler with a transparent learned profile.
- Add a verifier model behind the same provider interface.
