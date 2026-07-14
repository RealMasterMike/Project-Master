# Task: Plugin System

**Status:** Planned

## Objective

Allow capabilities to be added without modifying the core while preserving permissions and provenance.

## Next steps

- [ ] Define manifest schema.
- [ ] Implement plugin discovery.
- [ ] Add capability permissions.
- [ ] Add sandbox strategy.
- [ ] Add compatibility tests.

## Completion evidence

- Plugins declare capabilities before use.
- Plugins cannot silently expand permissions.
- Failures do not crash the core.

## Governing principle

Changes must improve reliability or clearly enable a later reliability improvement.
