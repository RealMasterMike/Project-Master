# Security Policy

Project Master v0.1 intentionally excludes unrestricted shell execution.

Report vulnerabilities involving:

- workspace path escape;
- unintended file writes;
- prompt injection through tool output;
- secrets leaking into logs or memory;
- unsafe deserialization;
- unauthorized network access.

Do not store API keys inside prompts, committed configuration files, or the SQLite database.
