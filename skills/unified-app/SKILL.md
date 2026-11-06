---
name: unified-app
description: "Operate Amplifier Unified through app_control: settings, tasks, collaboration, testing, outputs, Canvas, computer access and Smart Tools. Load only the reference needed for the user's task; ordinary conversation and actions whose schema is already known do not require this skill."
---

# Operate Amplifier Unified

The mounted `app_control` tool and current action schemas are the source of truth.
This skill supplies examples, not additional permissions or mandatory preflights.
Discover one relevant action prefix; reuse its schema until the host reports a
change. Follow returned pagination rather than gathering the entire catalog.

Read only the companion needed for the next action, relative to this skill's path:

| Task | Reference |
| --- | --- |
| Settings, appearance, bundles or runtime configuration | [settings.md](settings.md) |
| Peers, independent workers, tasks, questions, schedules or handoffs | [collaboration.md](collaboration.md) |
| File access, dependencies, resource ownership or isolated checks | [testing.md](testing.md) |
| Computer or browser access | [computer.md](computer.md) |
| Output attachments, images, exports or publishing | [outputs.md](outputs.md) |
| Interactive documents, diagrams, previews or Canvas | [canvas.md](canvas.md) |
| Smart Tools or MCP Apps | [smart-tools.md](smart-tools.md) |

For substantial coordination load `coordinate-work`; for shell implementation
load `amplifier-shell`; for CSS theme authoring load `appearance-authoring`, when
available. A missing optional skill does not block supported controls. Preserve
selected skill sources, exclusions and budgets. Do not load every reference at
startup or copy this manual into workspace scratch or personal memory.

Carry the authorized task through ordinary repair and focused validation. Report
a result or concrete external blocker. Reconcile an unknown mutation through its
saved receipt before retrying; do not recreate work merely to get a cleaner log.
