# Results and Resource Handoff Contract — v1 (ACCEPTED DIRECTION)

How completed changes reach another task or preview without losing unfinished work.

## Who builds against this

Integration and preview owners consume results from chat/helper runtimes.
The existing command, artifact, worktree, and resource services retain their
authoritative records. Users rely on those records when reviewing or cleaning up.

## What it is

A result names a preserved snapshot and the evidence produced against it.
An inclusion receipt names a destination and its resulting revision.
Resources record ownership separately from whether they still exist.

```json
{"resultId":"result1","changeSetId":"change1","snapshotId":"s1",
 "checks":[{"name":"app-tests","status":"passed","snapshotId":"s1"}]}
{"receiptId":"r2","resultId":"result1","destinationId":"preview1",
 "status":"included","destinationRevision":8}
{"resourceId":"preview1","ownerId":"chat1","status":"active","hostId":"example-host"}
```

## The promises

1. **Results identify preserved work.** A handoff captures committed changes,
   supported dirty files, artifacts, and repository baselines before release.
   A summary alone never stands in for recoverable work for the receiving owner.

2. **Inclusion names its destination.** Ready, included in another change set,
   running in a preview, and merged upstream are distinct states. Multi-repo
   publication reports partial results instead of pretending Git commits are atomic.

3. **Integration validates the combined result.** One owner combines each target's
   contributions and checks the resulting snapshot. Source conflicts return to an
   accountable owner; a helper's passing tests do not prove the integrated version.

4. **Preview updates respect separate authority.** A running preview uses an
   identified snapshot and one authorized updater. Preview policy never grants
   release, production deployment, or authority over another host implicitly.

5. **Unknown effects remain unresolved.** Lost acknowledgments retain a stable
   receipt and unknown state. Retry returns that record; restart does not replay
   work. Ownership reconciliation follows the existing reviewed handoff protocol.
   Fresh worker commissioning uses the existing checkout and task receipts to
   settle preparation before the initial input. A completed checkout can be
   adopted after restart from its exact receipt; an unconfirmed filesystem
   operation is retained and never repeated merely to make the worker active.

6. **Cleanup follows actual ownership.** Resources distinguish created/owned,
   borrowed, reaped, and observed absent. Cleanup consults current references,
   active owners, dirty work, unpublished commits, and unresolved receipts.

7. **Archiving preserves valuable history.** Archiving removes active navigation
   without deleting conversations, artifacts, or unfinished changes. Removing a
   working copy is separate and reports blockers rather than deleting the user's original.

## Not in v1

- **Cross-host migration** is promoted only when the portability service supplies
  destination prerequisites, writer transfer, and lossless original-preservation evidence.
- **Automatic external teardown** is promoted for each resource adapter after its
  ownership checks and uncertain-effect recovery can be demonstrated.

## How the kit checks it

- P1: transfer staged, unstaged, untracked, and document-draft fixtures; recover bytes.
- P2: fail the second repo publication; inspect each actual destination state.
- P3: integrate individually passing but incompatible changes; require a failed gate.
- P4: attempt preview updates from a helper and an unrelated chat; verify refusals.
- P5: lose acknowledgment and restart; count external effects and inspect the receipt.
- P6: try cleanup with borrowed resources, active previews, dirty work, and unknowns.
- P7: archive a chat and remove an eligible checkout; reopen its history and outputs.

## Open questions

- How long should eligible idle working copies be retained by default before
  cleanup, and what local storage pressure should surface a user choice?

## Changelog

| Date | Change | Evidence |
|---|---|---|
| 2026-09-22 | Initial handoff and resource rules. | Reviewed PR integration, Spark preview ownership, and amplifier-workspace manifest semantics. |

Design direction accepted by the user on 2026-09-22. Acceptance is not a claim of implementation conformance; see [implementation status](../IMPLEMENTATION.md).
