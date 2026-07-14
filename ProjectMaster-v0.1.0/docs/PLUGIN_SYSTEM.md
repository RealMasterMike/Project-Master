# Plugin System

A future plugin contains:

- manifest and version;
- declared capabilities;
- tool schemas;
- permission requirements;
- configuration schema;
- health check;
- audit and provenance behavior;
- tests.

Plugins should be sandboxed where practical and must not silently gain capabilities after installation.
