# Shared-task history without a lifetime ceiling

**Implemented:** per-item storage, paged observation and creation without a
lifetime task-count ceiling. The
[shared-task contract](contracts/execution.v2-candidate.md#shared-task-lifetime-and-observation-revision--accepted-direction)
owns the observable promises. Completed and cancelled work remains available
without exhausting the assignment's ability to create new tasks, following
[VISION 11](../architecture/VISION.md#11-available-resources-govern-throughput).
Existing Stop, claims, operator-selected execution limits and storage failures
retain their meaning; this does not automatically start work or expire history.

## Owner and storage

Keep `AssignmentTasks` and `Assignments` as the owners. Store each current item
in the existing `commands` table, following the current Work-record precedent;
retain historical command receipts unchanged. Do not add an archive copy,
second task index owner or history expiry. Derived SQLite indexes serve the
actual active/history/claim queries. Keep task reports and reference strings
inline; references do not become artifact pins.

Exact reads fetch an item and its declared dependencies. Dependency edits check
the reachable graph, which can include a long chain of completed prerequisites;
unrelated terminal history does not enter that traversal or ordinary creation.
Task changes, assignment revision and command receipt commit together under the
existing mutation owner. Retirement, Work links/invocation and workspace review
must consume that owner; a partially inspected review cannot claim no stale work.

Convert a legacy assignment on its first authorized task mutation, atomically,
preserving its IDs and results. Passive reads need no migration or startup sweep.
The historical 128-item bound still validates accepted embedded maps during
conversion; it does not limit normalized task records. Validate record-key
conflicts before replacing the embedded authority. An exact
retry must return its original result without repeating conversion or effects.

## Response transition and consumers

Use a documented minor release while Unified is on `0.x`, under the existing
[release policy](../validation/release-qualification.md#version-choice-and-release-notes).
Update the bundled task UI, assignment controls, action discovery and specialist
coordination guide together. Replace whole-board reads with active/history pages
and exact details, including off-page dependency selection and preserved drafts.
Historical control receipts can still contain the old board; do not rewrite them.
Current reads and newly accepted control results use task references instead.

`assignment.tasks.list` defaults to `view: active`; `history` selects completed
and cancelled items. `tasks` contains summaries, not full descriptions or reports.
The default page has at most 8 items (`limit` accepts 1–16); its task summaries
fit within 9 KiB of JSON encoded as UTF-8 without escaping non-ASCII characters.
HTTP escaping can use more bytes. The existing member/control envelope is
additional and still follows retained roster size. This is not a total-response
byte bound.
Follow `nextAfter` as `after` with the same view. Pages follow creation time and
task identity, not a retained snapshot. `coverage.changed` reports changes since
the first page; refresh to include tasks that entered the view before the cursor.

Use each `detailRef`, or `assignment.tasks.read` with an exact `taskId`, for the
full item and its direct prerequisite summaries. Off-page and unavailable
prerequisites remain explicit. `assignment.tasks.wait` takes the same page
arguments and its `version` as `afterVersion`; holder activity still changes that
observation without changing a claim. New assignment-control results expose a
caller-scoped `taskListRef`. Historical retries may still return the original
whole-board result; those results never replace current authority.

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
release with explicit migration and matching-reader rollback guidance. The later
paged-response transition also ships in a minor release with bundled consumers
updated together; it does not rewrite the storage format or old receipts.

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
3. Implemented: indexed pages/detail/wait and coordinated bundled consumers/guidance,
   with the existing 128-item creation ceiling retained.
4. Implemented: no lifetime creation refusal, with continuation beyond 128 completed
   or cancelled tasks, exact retries, restart and matching-reader backup recovery.

Extend maintained task, retirement, Work/review and backup tests for interruption,
restart, exact retry, old-reader refusal and more than 128 sequential completions.
At fixed unfinished work, grow same-assignment terminal rows and report bytes;
measure SQL work plus decoding/encoding, not only response size. Qualify one built
client journey covering pages, dependencies, drafts and lost acknowledgements in
an owned DTU. Measure derived-index first-build cost separately from warm reads. This
adds no broad release gate and does not claim to bound the retained member roster.
