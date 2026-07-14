# Tool Layer

## v0.1 tools

- `calculator`: safe arithmetic through an AST evaluator.
- `current_time`: local machine time.
- `workspace_list`: list files below the configured workspace root.
- `workspace_read`: read a UTF-8 text file below the workspace root.
- `workspace_write`: write a text file when explicitly enabled.
- `memory_remember`: store contextual memory with source and confidence.
- `memory_recall`: search stored memory.
- `claim_record`: add an unverified or assessed claim.
- `evidence_add`: attach supporting, contradicting, or contextual evidence.
- `claims_list`: inspect the evidence ledger.

## Rules

- Tools return data; the model must still interpret it.
- Errors are visible to the model.
- Paths are resolved and checked against the workspace root.
- Write capability is disabled by default.
- No unrestricted shell execution is included.
