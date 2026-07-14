# Declarative UI Customization

Project Master treats interface customization as controlled state, not generated source code. The
application owns a versioned layout document and a registry of panels, allowed containers, width
limits, and capabilities. A user—or later, an AI tool—can request only operations defined by that
registry.

## Phase 1 behavior

- panel visibility and collapsed state;
- constrained panel widths;
- ordering and tab-group fields for future panels;
- local persistence;
- up to 50 in-session Undo steps;
- Reset to default;
- up to 20 named saved layouts;
- transactional validation: an invalid operation rejects the entire command.

The first vertical slice is the tabbed **Customize** panel. It proves collapse, resizing, saved
layouts, recovery, and persistence without exposing application code to the model.

## Command contract

```json
{
  "schemaVersion": 1,
  "baseRevision": 4,
  "operations": [
    {
      "operation": "set_collapsed",
      "target": "customize_panel",
      "value": true
    },
    {
      "operation": "set_width",
      "target": "chat_panel",
      "value": 80
    }
  ]
}
```

`baseRevision` prevents a stale request from overwriting a newer user change. Every accepted batch
advances the revision once. Unknown panels, unsupported operations, invalid containers, out-of-range
widths, forbidden states, and batches larger than 32 operations are rejected.

## Security and recovery boundary

The layout interface cannot execute or store arbitrary CSS, HTML, JavaScript, React components, file
edits, or shell commands. The required chat panel cannot be hidden or collapsed. Corrupt or obsolete
stored state falls back to the default layout instead of preventing startup.

## Later phases

Natural-language control is intentionally not part of Phase 1. A future model tool may translate a
request into this command contract, but the application will still validate, preview, apply, save,
undo, and reset every change. Generated components or plugins remain a separate, sandboxed future
capability and must never rewrite the production interface directly.
