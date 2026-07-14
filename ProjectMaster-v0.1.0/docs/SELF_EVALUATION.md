# Self-Evaluation

A future verifier should inspect an answer independently from the producing model.

Checks should include:

- unsupported certainty;
- claims not entailed by cited evidence;
- source-chain duplication;
- unmarked inference or speculation;
- contradictions with stored evidence;
- failure to answer the question;
- claims that a tool action succeeded without verification.

v0.1 includes a deterministic linter so this concept is testable without doubling model usage.
