# Workspace Execution Contract — v1 (ACCEPTED DIRECTION)

How chats use their chosen folders and coordinate concurrent file changes.

## Who builds against this

The execution/worktree service allocates working areas and writer ownership.
The runtime, helpers, editor adapters, and developers consume those bindings.
Existing Foundation ownership and Unified handoff receipts remain authoritative.

## What it is

A change set groups one piece of ongoing work across repository checkouts or
document drafts. Its execution binding carries a revision and one writer owner.
An isolated working directory is not a security sandbox.

```json
{"changeSetId":"change1","sessionId":"native-session-1","mode":"workspace-folder",
 "bindings":[{"repositoryId":"repo1","baseCommit":"abc123","checkoutId":"co1"}],
 "writer":{"ownerId":"worker1","generation":7},"executionRevision":4}
{"helperId":"helper1","parentId":"native-session-1","access":"read-snapshot",
 "snapshotId":"snapshot1","scratchId":"scratch1"}
```

## The promises

1. **Reading does not allocate writers.** Discussion and inspection use identified
   source snapshots and separate scratch space. A chat does not create branches
   merely by opening, and a reviewer cannot alter its reviewed source binding.

2. **Chosen folders are real working folders.** Ordinary chat edits use the
   selected workspace folder. A terminal or editor there sees the same files.
   Attachment cannot silently substitute a hidden working copy for the user's folder.
   An explicit move to an associated checkout verifies that checkout's recorded
   repository. Returning to the exact canonical home remains valid when that home
   is a non-Git folder containing several repositories.
   A commissioned task can leave its captured inherited execution folder and
   recover that exact source after an uncertain move. This does not transfer
   checkout ownership or permit choosing another task's checkout as a new target.

3. **One checkout has one writer.** Owner generations fence managed writes.
   Old processes must be quiesced or reconciled before reassignment. Stale writes
   must be refused for the new owner's safety. External editors remain observable
   participants; the service does not pretend to lock out unmanaged tools.

4. **Helpers inherit a bounded assignment.** A helper has a real parent, scope,
   result destination, and read/write mode. It cannot use workspace membership
   to command unrelated chats or update their preview targets.

5. **Concurrency choices belong to tasks.** Conflicting writes to one folder
   queue by default. Authorized independent parallel work can use separate copies,
   with the task path visible and a terminal opening there. One manager may
   commission many independent durable workers in parallel. Explicit isolated
   commissioning creates a managed checkout and saves the worker's objective
   before admitting its first working input; no model setup turn or later
   relocation is needed. Existing worktree and task receipts own these steps,
   and a partial preparation retains its root, files and outcome for recovery.
   Clean committed-source creation binds an explicit immutable commit separately
   from working-folder snapshots. It must not scan unrelated local changes or
   untracked evidence. Explicit dirty-copy creation retains its full snapshot,
   size and conflict checks. A confirmed pre-admission checkout refusal is
   reported as not sent; uncertain admitted effects remain unknown and are not
   retried merely to create activity. The retained idle worker can use the
   existing guarded placement and fresh-input path after reconciliation.
   Read-only helpers may retain the inherited placement. Folder attachment
   never asks the user to choose an execution mode or silently relocates a chat.

6. **Bindings govern the whole runtime.** Shell cwd, file tools, dependency
   loading, tests, and supported preview launchers use the same recorded change
   set. Falling back to another task's mutable checkout fails the developer's check.

7. **Document work preserves the original.** For non-Git files, changes use owned
   drafts and an explicit destination/version check. An unsupported writer tool
   cannot silently overwrite a shared source while reporting isolated execution.

## Not in v1

- **Hard isolation from arbitrary shell programs** is promoted through a real
  filesystem/container enforcement backend. Path bindings alone do not prove it.
- **Automatic background relocation** requires an explicit task-level experience
  and authorization; file-ownership hints do not override the single-writer default.

## How the kit checks it

- P1: inspect a repository and assert no branch, checkout, or source mutation.
- P2: edit through a workspace chat and read the same file from its terminal/editor.
- P3: restart, expire a writer, and attempt writes using the old generation.
- P4: attempt a peer mutation from a helper and require a scope refusal.
- P5: race two writers; verify queuing, then authorize separate work and inspect cwd.
- P6: inspect shell, imports, tests, and preview provenance in a two-repo fixture.
- P7: race an original document update against accepting a generated draft.
- P8: commission ten independent workers through the canonical create action;
  assert overlapping preparation, separate checkout ownership and saved objectives before first input,
  one source refusal with an explicit correction, restart without duplicate
  creation, and Stop suppressing unadmitted work. A separate real Core/Live smoke
  observes the saved goal and isolated working-directory capability at the first
  scripted provider request, then completes through the canonical task action.

## Open questions

- Which first-release tool adapters can enforce isolated non-Git document writes,
  and which must report an unsupported operation instead of implying protection?

## Changelog

| Date | Change | Evidence |
|---|---|---|
| 2026-09-22 | Initial execution contract. | Successful shared-clone/worktree workflow and observed shared-environment limitations. |
| 2026-09-22 | Folder-first execution; task-level concurrency only. | User rejected attachment-time developer modes and required terminal-visible edits. |
| 2026-10-14 | Clarify selected-repository handoff and non-Git canonical home return. | Multi-repository handoff acceptance exposed a Git-home assumption. |

Design direction accepted by the user on 2026-09-22. Acceptance is not a claim of implementation conformance; see [implementation status](../IMPLEMENTATION.md).
