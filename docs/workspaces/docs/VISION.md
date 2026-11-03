# Unified Workspaces — Vision (ACCEPTED DIRECTION)

This page describes the end state as though it already exists.
It does not record implementation progress or release status.
Evidence and current gaps live in the acceptance note.
A change of direction begins with an amendment here.
The changelog records its reason.
Implementation work follows the agreed direction.

## What Unified Workspaces is

A workspace is a lasting home for related conversations, files, and work.
An information worker names it and starts asking for help.
A developer can connect existing folders and repositories without reorganizing them.
Unified prepares the places where work happens and keeps each result connected
to the conversation, sources, and people or agents responsible for it.
Users can inspect and steer that arrangement without needing to configure it first.

## Principles

### 1. Start with the purpose

A workspace begins with a name and a sensible location supplied by the app.
An ordinary chat begins with a message. Neither path requires the user to choose
a repository strategy, model, agent roster, or branch before describing the work.
Technical choices appear when they become useful or when the user asks for them.

### 2. Keep existing work intact

Existing folders remain where their owners put them. Opening or attaching one
does not reorganize it, initialize a parent repository, or absorb local edits into
another task. Finding a familiar name is not permission to claim a folder.
The placement contract owns the checks that distinguish creating from attaching.

### 3. Let names remain human

A workspace's name expresses its purpose. Its identity survives a rename and
does not depend on a host's path spelling. A location is an explicit association
with a machine and account. Identical names do not make two folders the same place.
Details expose full paths whenever someone needs to distinguish or inspect them.

### 4. Keep chosen folders predictable

Working in a folder means editing its files, visible to a terminal opened there.
Competing writers wait or receive separate task working areas when independent
parallel work is authorized. The task exposes that location and its results.
Attaching a folder never requires a developer mode or hides a change of directory.

### 5. Keep the conversation central

The sidebar helps users find conversations and their workspaces. Outputs,
sources, helpers, and previews belong beside the conversation that uses them.
Opening their details leaves the draft, selection, and ongoing work intact.
Inspecting work never quietly grants permission to start or redirect it.

### 6. Expose facts on demand

The default view reports work, results, and decisions in ordinary language.
Details reveal the responsible agents, source revisions, checks, locations,
and unresolved effects. The simple and advanced views describe the same state.
An attractive summary cannot turn an unverified result into a completed one.

### 7. Preserve what people value

Conversations, outputs, decisions, and unfinished changes survive task cleanup.
Web, CLI, and TUI share native session identity and history as their source of truth.
An app index helps find those sessions; it does not decide which ones exist.
Temporary working files have a separate lifetime from their workspace.
A removed folder can become unavailable without erasing its history.
External resources retain ownership and cleanup records until their state is known.

### 8. Make steering equally available

A user can ask an agent to perform any supported workspace action or invoke it
through the interface. Both paths use the same permissions, identities, revisions,
and receipts. Developer controls add precision to those actions; they do not
unlock a second implementation with weaker preservation guarantees.

### 9. Managers amplify independent work

One manager can prioritize and coordinate many independent goal-driven workers,
and a higher-level manager can coordinate other managers. A worker receives a
complete objective, acceptance criteria, authority and suitable working location
before its working turn. It carries ordinary implementation, repair, isolated
validation and the reviewable result through completion without step-by-step
manager approvals. Managers review results and resolve genuine cross-task decisions.
The host owns placement, task state, result delivery and accepted-task closure;
models should not reconstruct those mechanisms through repeated conversations.
Actual Stop, authority, writer ownership and unknown effects retain their meaning.

Shared scratch is a short current context and index. Saved tasks and receipts own
status; user instructions and current permissions own authority. Detailed history
and evidence are loaded for a decision, not copied into every worker request.
Useful superseded history stays outside automatically loaded instructions; stale
holds and obsolete duplicate notes do not become permanent operating rules.

## What this deliberately resists

- **A Git setup wizard for everyone.** Repository mechanics belong in execution
  services and optional developer details, not the first-use path.
- **Mandatory disposable project containers.** Working copies can expire;
  the history service owns durable conversations and artifacts.
- **One global pile of agent notes.** Task notes stay with their task;
  shared decisions enter the workspace context through an attributed action.
- **Implicit cloud or cross-host migration.** The portability service owns transfer
  and destination readiness. Workspace navigation does not pretend to provide it.
- **A hidden deployment side effect.** A preview owner's authorized policy governs
  preview updates. Release and production authority remain separate.

## How you can tell it is working

- A new information worker creates a named workspace and submits its first request
  in under 60 seconds without entering a filesystem path.
- A returning developer attaches 2 existing repositories with 0 changes to their
  original files, branches, or remotes during attachment.
- A CLI/TUI user opens 1 existing workspace and finds its saved native sessions
  with 0 imports, duplicate conversations, or replacement session IDs.
- A project owner runs 2 chats with 2 writing helpers each and observes 0 unintended
  overwrites in the fixture containing overlapping source edits.
- A reviewer opens an output or helper report within 2 actions from its chat,
  with 0 changes to the unsent composer text.
- An operator identifies the owner, host, and deployed revision of 1 shared preview
  within 30 seconds using only its details panel.
- A manager commissions 10 independent workers and receives results or genuine
  blockers without a setup-only model turn or routine acknowledgement turns.
  Restart and one correction retain the same owners and completed effects.
- Shared scratch changes reflect current decisions; archived receipts and stale
  instructions do not enter ordinary requests unless explicitly requested.
- A user recovering from 1 interrupted handoff finds the original conversation,
  artifacts, and unfinished changes with 0 replayed external actions.

## Changelog

| Date | Change | Evidence |
|---|---|---|
| 2026-11-03 | One manager coordinates many autonomous workers; current shared context stays separate from history and task state. | User-requested workflow simplification and scratch-context cleanup. |
| 2026-09-22 | Initial proposed direction. | User's name-first/root-setting request, supplied sidebar examples, inspected Codex task workflows, and current Unified/workspace-tool source. |
| 2026-09-22 | Preserve folder semantics and native history across clients. | User's attachment critique and explicit shared-session source-of-truth requirement. |

Design direction accepted by the user on 2026-09-22. Acceptance is not a claim of implementation conformance; see [implementation status](../IMPLEMENTATION.md).
