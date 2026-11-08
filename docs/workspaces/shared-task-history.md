# Shared-task history without a lifetime ceiling

**Accepted direction; storage batch implemented, response transition pending.** The
[shared-task contract](contracts/execution.v2-candidate.md#shared-task-lifetime-and-observation-revision--accepted-direction)
owns the observable promises. Current source lazily separates current tasks into
individual records and uses exact internal reads. It retains the 128-item ceiling
and bounded whole-board public responses, including after earlier tasks finish.
Paging and removal of that lifetime ceiling remain separate batches. Removing
only that refusal would make public reads and responses grow
with completed history, contrary to [VISION 11](../architecture/VISION.md#11-available-resources-govern-throughput).

## Owner and storage

Keep `AssignmentTasks` and `Assignments` as the owners. Store each current item
in the existing `commands` table, following the current Work-record precedent;
retain historical command receipts unchanged. Do not add an archive copy,
second task index owner or history expiry. Derived SQLite indexes serve the
actual active/history/claim queries. Keep task reports and reference strings
inline; references do not become artifact pins.

Exact reads fetch an item and its declared dependencies. Dependency edits check
the affected graph; unrelated terminal history should not enter ordinary work.
Task changes, assignment revision and command receipt commit together under the
existing mutation owner. Retirement, Work links/invocation and workspace review
must consume that owner; a partially inspected review cannot claim no stale work.

Convert a legacy assignment on its first authorized task mutation, atomically,
preserving its IDs and results. Passive reads need no migration or startup sweep.
Validate record-key conflicts before replacing the embedded authority. An exact
retry must return its original result without repeating conversion or effects.

## Response transition and consumers

Use a documented minor release while Unified is on `0.x`, under the existing
[release policy](../validation/release-qualification.md#version-choice-and-release-notes).
Update the bundled task UI, assignment controls, action discovery and specialist
coordination guide together. Replace whole-board reads with active/history pages
and exact details, including off-page dependency selection and preserved drafts.
Historical control receipts can still contain the old board; do not rewrite them.
Current reads and newly accepted control results use task references instead.

The independently released [TUI](https://github.com/microsoft/amplifier-app-tui/tree/d6e292a7788d6418820d6bf362390ab3d950a4d8)
(`v0.4.0rc2`; current bridge also inspected at `96b3d138`) and
[Mac client](https://github.com/microsoft/amplifier-client-macos/tree/ca6997778b2c7dd889042bf7e4dfdb79c7a094f8)
(`v0.1.4`) have no shared-task response consumer in their inspected transport,
command and display owners. The generic Python adapter forwards responses, and
the repository shell SDK declares no task-response shape. This source inventory
does not certify client/host runtime compatibility or enumerate private custom
clients. Add a legacy route only for an identified consumer with a bounded need;
never silently return a prefix or retain an unlimited whole-board fallback.

## Reader and backup boundary

Converted assignments use the distinct `assignment.record.v2` kind and
`records-v1` task layout. An older reader refuses a converted assignment when
accessed; this is not a whole-host startup refusal. Restart and backup restore
require a matching reader. This storage-format transition ships in a minor
release with explicit migration and matching-reader rollback guidance, even
though public responses remain unchanged in this batch.

The existing private backup owner captures all `app.sqlite3` rows from one WAL
snapshot. Keeping current task records and command receipts there preserves their
transaction boundary without a full backup per task. Qualify restoration with the
matching reader. Transcript import/export does not restore assignment records;
the old `shared-storage-v1` backup is not a fresh rollback point. Preserve later
work before choosing an earlier snapshot; lossless downgrade after new writes
is not promised. Explicit permanent deletion/reset keeps its existing owner and
must not strand newly separated task payloads.

## Reviewable batches and proof

1. Recorded: this contract/consumer transition.
2. Implemented: per-item records, atomic conversion and exact internal consumers,
   retaining the existing ceiling and response behavior for bounded old boards.
3. Pending: indexed pages/detail/wait and coordinated bundled consumers/guidance.
4. Pending: remove the lifetime refusal after those paths qualify; ship the coherent
   response change as a minor release.

Extend maintained task, retirement, Work/review and backup tests for interruption,
restart, exact retry, old-reader refusal and more than 128 sequential completions.
At fixed unfinished work, grow same-assignment terminal rows and report bytes;
measure SQL work plus decoding/encoding, not only response size. Qualify one built
client journey covering pages, dependencies, drafts and lost acknowledgements in
an owned DTU. Exact page fields/budgets need that implementation evidence. This
adds no broad release gate and does not claim to bound the retained member roster.
