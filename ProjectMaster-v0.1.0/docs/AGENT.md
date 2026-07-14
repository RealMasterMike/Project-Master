# Agent and Autonomy

## v0.1

The agent is a synchronous tool-calling loop. It can request a tool, inspect the result, and continue reasoning.

## Future autonomy requirements

Before scheduled or multi-step autonomy is enabled, Project Master needs:

1. explicit plans;
2. scoped permissions;
3. dry-run previews;
4. completion verification;
5. rollback or recovery where practical;
6. durable task state;
7. visible logs;
8. cancellation controls.

The system must distinguish “attempted,” “partially completed,” and “verified complete.”
