# Workspace Context Contract — v1 (ACCEPTED DIRECTION)

How conversations share a project without sharing every draft or changing its history.

## Who builds against this

The workspace/context service owns associations. The runtime, history adapters,
and conversation clients consume them. Repository owners and users must be able
to tell which instructions, files, and decisions a particular task received.

## What it is

Workspace history is derived from native sessions under the history contract.
Shared references and repository bindings add context without defining another
chat catalog. A repository is identified separately from any one task checkout.

```json
{"workspaceId":"w1","revision":3,
 "repositories":[{"repositoryId":"repo1","role":"app","locationId":"l1"}],
 "references":[{"artifactId":"brief1","revision":2}],
 "instructions":[{"source":"workspace-directions","revision":1}]}
{"sessionId":"native-session-1","workspaceId":"w1","historyHome":"unchanged",
 "executionId":"execution1","contextRevision":3}
```

## The promises

1. **Workspace context preserves native history.** A workspace view follows its
   canonical native project. Display metadata and references preserve session IDs,
   drafts, voice, and artifacts. A private app membership list cannot hide or
   reassign a user's CLI/TUI conversations; the history contract owns discovery.

2. **Repositories retain independent identity.** A workspace can bind zero,
   one, or several repositories without a parent Git repo or forced submodules.
   Existing repository and submodule layouts stay valid for their owners.

3. **Context has explicit provenance.** Runtime context identifies workspace,
   task, repository, and relevant subtree instructions. User directions and
   host policy retain priority; generated context never overwrites an AGENTS.md.

4. **Private drafts stay private.** Another chat's unfinished edits, composer,
   and full transcript are not ambient shared context. An explicit reference or
   authorized collaboration supplies a named snapshot to its recipient. Even the
   calling chat's unsent composer is excluded from ordinary app readback. An
   explicit `/view/draft` read must name a client displaying that calling chat
   and is intended only for user-requested draft work.

5. **Shared decisions have attributable updates.** Task notes belong to their
   task. Promoting a decision records source, revision, and actor through one
   concurrency-safe action. Competing writers cannot silently lose user context.

6. **Repository changes bind exact versions.** Each executing change set records
   its repository revisions and local dependency bindings. A task cannot silently
   import a sibling chat's editable dependency while claiming its own snapshot.

7. **Incomplete imports remain visible.** Adding repositories reports each one's
   state and preserves successful work when another fails. Retry uses the same
   receipt; a multi-repo import is not called complete while one binding is absent.

8. **Always-present context is orientation, not a manual.** Include durable user
   preferences, applicable instructions, the current objective and essential
   discovery/authority semantics. Detailed recipes and specialist workflows use
   existing skills, schemas and exact references when relevant. Preserve selected
   sources and exclusions; missing optional guidance does not disable supported
   actions. Do not silently truncate user instructions to meet a prompt budget.

9. **Guidance follows explicit scope and stays current.** Ordinary roots bind the
   associated workspace and actual execution directory separately, with source
   attribution and duplicate suppression. Do not walk unrelated ancestor folders.
   File-relative includes such as `@./SCRATCH.md` resolve beside their declaring
   file, not accidentally inside a separate execution checkout.
   Read bound files afresh through the native context owner; snapshots retain their
   deliberate frozen semantics. Reuse already-read repository guidance until its
   relevant source or scope changes, rather than rereading at each work phase.

10. **Notes do not become competing control systems.** Shared scratch holds only
    current cross-task decisions, unresolved blockers with owners, and ordinary
    links. Task details, transcripts and closed-work evidence stay on demand.
    Personal memory records stable preferences, not obsolete holds, repeated
    reminders, global task procedures or unbounded execution history. Preserve
    explicit user choices and concurrent edits when simplifying existing files;
    starter revisions never silently rewrite user-authored guidance.

11. **Autonomy includes ordinary repair.** A manager can commission many workers
    with complete objectives and existing authority. Workers return completed
    deliverables or concrete external blockers, not routine acknowledgements.
    Actual Stop, permissions, ownership and uncertain-effect recovery remain with
    their canonical controls. Prompt text must not invent another approval gate.

## Not in v1

- **Automatic personal memory extraction** is promoted through the separate
  memory controls and acceptance program, not workspace membership alone.
- **Universal submodule reproduction** is promoted by a supported fixture needing
  dirty nested submodule transfer. Unsupported layouts receive an explicit result.

## How the kit checks it

- P1: attach a folder with CLI/TUI chats, voice, and outputs; compare native IDs.
- P2: bind document-only, single-repo, multi-repo, and existing-submodule fixtures.
- P3: inspect assembled context and prove source instructions were not rewritten.
- P4: create private peer work and inspect another chat's admitted context.
- P5: race two shared-decision edits and require a revision conflict or both records.
- P6: change a dependency in another task and inspect the executing import path.
- P7: fail one of two imports, retry, and compare stable binding/receipt IDs.
- P8–P10: inspect a real assembled request for source attribution, size, duplicate
  imports, changed scratch, an unrelated ancestor and linked historical records.
  Verify packaged skills and companions remain discoverable without loading their
  bodies on every request. Track the shipped orientation budget in a focused test.
- P11: qualify representative routine repair, independent worker and recovery
  flows. Compare avoidable questions, reads and waiting while preserving the same
  acceptance requirements. A smaller prompt alone is not proof of better behavior.

## Guidance maintenance

Every new always-on instruction needs an owner, applicability and a reason it
cannot live in a schema, skill or enforced mechanism. Remove superseded advice
when its underlying defect or missing capability is repaired. Prefer clear goals
and exceptions over growing lists of mandatory steps. Runtime capability and
always-on procedure are separate design decisions; do not remove useful tools
merely to reduce their awareness text.

This direction follows current [OpenAI prompting guidance](https://developers.openai.com/api/docs/guides/latest-model),
[Anthropic context engineering](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents)
and [instruction-file guidance](https://code.claude.com/docs/en/memory), reviewed
2026-11-06. Their recommendations inform tests; they do not override user choices
or substitute for measured behavior in this application.

## Open questions

- Which shared-decision updates should workspace policy authorize automatically,
  and which should remain suggestions until the user accepts them?

## Changelog

| Date | Change | Evidence |
|---|---|---|
| 2026-11-06 | Add task-scoped context, fresh explicit bindings and guidance maintenance (P8–P11). | User-approved operating principles; implementation and behavioral evidence remain separate. |
| 2026-09-22 | Initial context and multi-repository boundaries. | Inspected multi-repo workflows, instruction template, and existing native-history separation. |
| 2026-09-22 | Delegate chat identity and discovery to shared native history. | User ruled out an independent workspace chat catalog. |

Design direction accepted by the user on 2026-09-22. Acceptance is not a claim of implementation conformance; see [implementation status](../IMPLEMENTATION.md).
