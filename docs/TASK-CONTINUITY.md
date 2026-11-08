# Saved task and context continuity

The Goals & modes page offers a saved task, retaining objective, constraints,
revisioned corrections, status, question IDs, operation IDs, artifact references,
and explicit completion evidence. UI and `app_control` use the same `task.*`
actions: `get`, `create`, `update`, `pause`, `resume`, `block`, `complete`.
Agent actions are bound to the calling conversation, including the legacy
`runtime.control` forwarding alias. The narrow assignment-owner controls below
are separate from general cross-chat task editing. These controls do not send input, change a
draft/selection, grant permissions, resolve questions, or create a scheduler.

Mutations require exact `expectedRevision` and an idempotent dispatch command ID.
The worker serializes edits and stores state and command receipts in the existing
app-owned `sessions/<id>/control-state.json`. Completed task records remain in
history when a new objective is created. An existing unfinished task is edited
rather than replaced. Task actions return authoritative state, unlike a queued
management acknowledgement. Linked operation IDs are observations; unknown
outcomes remain unknown until their actual evidence is reconciled.

`task.waitForUpdate` is the narrow host-owned exception to lifecycle-only edits:
an owner explicitly yields its active goal for the exact staged app target and
requests one guarded continuation after verified adoption. It never rewrites
task status, revision, continuation epoch or budgets, and therefore preserves
the original return bindings of admitted peer work. Its host command receipt is
readable through `task.receipt`; checkpoint, adoption and input admission remain
distinct. Use the [owner-local update flow](UPDATES.md#work-continues-while-an-application-update-is-pending),
not generic pause/block plus an external resume. Stop and authority changes
still supersede the wait.

## Planning and required limits

A standing user request to complete work includes ordinary corrections,
verification and agreed handoffs within its scope. Managers can revise their own
planning checkpoints without requesting fresh user approval. Ordinary failure
correction and the affected focused checks remain part of that mandate; a manager
does not manufacture fixed attempt allowances or repeated approval cycles.
Estimates belong in planning, not in enforced limits or instructions that forbid
ordinary verification. Existing managed profiles and maintained project checks
should supply qualification; independent review focuses on changed risks and
original acceptance, with unchanged passing evidence reused. The optional
`maxTurns` field is an enforced limit, not an estimate; guidance therefore omits
it unless a hard limit is required. Explicit user, commissioning-owner and host
limits remain effective. An agent-origin record can contain a user's limit, and
unknown historical provenance is not reclassified as an adjustable plan.

The existing exact-revision `task.update` action can revise an authorized limit
without resetting accumulated goal turns or deleting corrections and evidence.
A completed objective remains immutable; `task.create` can record an already
authorized follow-up and retains the completed task in history. Neither action
supplies permission, resolves a pending user choice or overrides Stop. This is
operating guidance for existing controls, not a new grant or automatic migration.

An assignment owner can use `assignment.task.read` to inspect a bound member's
canonical task identity, revision, limit, used turns and exact limit-change receipt
without starting its worker. `assignment.task.setLimit` edits only `maxTurns` and
a retained correction through the existing native task controller. It requires
the current assignment owner, same bound member, exact assignment revision and
exact task identity/revision. The member does not need another model turn, even
when its current limit is exhausted. Preparation rechecks ownership, location,
Stop, configuration and authorization at control admission; native application
checks the exact task identity/revision. An already admitted control may finish,
but cannot clear a later Stop or resume work.

Increasing or removing an existing limit uses the manager's user-selected Full
access policy or an original user message / verified saved question answer
authorizing that correction. A saved answer remains usable after its answering
turn ends; the user need not repeat it. Generic agent origin does not classify a
legacy cap as a planning estimate. Full access does not change a deliberate cap
automatically: the manager still explicitly chooses and records the correction
within the user's mandate. In Ask mode, missing authorization leaves the cap
unchanged. Tightening a limit does not require additional permission.

The change retains the same task, accumulated turns, status, evidence, questions,
operation references and other budgets. It does not clear Stop/pause, resolve an
unknown effect, or send/replay input. Continuation uses the existing separate
controls and admission checks. Its exact command receipt stays in the existing
native control state, including manager and authorization provenance. After a
lost acknowledgement, read that receipt; absence is not proof that an in-flight
control did not commit. No new permission store or continuation loop is added.

`assignment.task.complete` lets the same assignment owner close an accepted
member contribution without asking the worker to spend another model turn.
Use the existing assignment revision, member task ID/revision and nonempty
completion evidence. The host reads the exact objective from canonical task state
and forwards ordinary `task.complete`; objective edits increment the revision and
replacement tasks have a different ID. The native controller requires idle work,
including no queued input or active children, and persists its normal completion
and manager provenance in the existing task receipt. Admission rechecks the same
ownership, location, Stop and settings boundary as the limit control.

Standing manager responsibility suffices: no fresh human answer or grant is
required for this acceptance record. A stopped, paused or dependency-blocked
member may be closed without clearing its Stop or sending input. Cold native
preparation may be needed to reach the task controller; it never starts a model
turn. History, used turns, limits, question/operation references, earlier failure
receipts and owned resources stay intact. The manager must verify that the exact
contribution is achieved; a diagnostic pass is not proof of its original feature
or incident being fixed. This is not resource cleanup, assignment retirement,
shared-task-list completion or closure of other objectives. Those existing controls
and responsibilities remain separate. Duplicate/readback and unknown responses
use the same existing command receipt, with no repeated model work.

An exact manager mutation retry also republishes the current saved task to the
host view, retaining the original receipt even when a newer objective now exists.
A failed host publication reports that uncertainty without hiding the committed
receipt. Passive task reads do not refresh the host view.

A new deliberate peer request using `queue`, `auto` or `steer` observes the
recipient's current saved task before capturing its input binding. This includes
a completed task whose acknowledgement was lost. The observation uses one bounded
read of that recipient's control file and the input's existing publication; it does
not read other chats or validate historical tasks. Unavailable optional observation
keeps the prior view and existing native admission checks. Already captured inputs
keep their original binding, and a native task change after capture still prevents
admission. This does not replay input, reopen completed work or reset used turns.

Cold preparation restores all saved controls before persisting them. It does not
write mounted defaults over still-unrestored budgets or modes while loading a
task. If saved-control restoration fails or is cancelled before its final
persistence, the original control file remains available for recovery; the limit
correction has not run. This is not rollback for failures during the final write
or later preparation. Successful restore and later mutations keep ordinary
persistence.

## Continuation and recovery

The existing goal controller performs continuation. The task revision applies at
the next `provider:request` boundary during running work. Pause/block/completion
clear the current goal immediately; the optional loop-live continuation guard
also rejects a stale continuation captured by an in-flight evaluator. A current
turn may finish. Explicit later user inputs still receive a response. Resume is
deliberate and does not send a new prompt. Durable task creation requires the
loop's `live.continuation_guard_supported` capability. Older loops fail clearly.
Legacy goal setters cannot replace an unfinished saved task. Completion requires
the exact current objective/revision and nonempty evidence; a model turn ending
or the goal judge stopping never automatically completes the saved task.

A dependency block recorded by the owning agent is distinct from human Stop or
task pause. A **new direct queued peer message** may receive one ordinary response
while that task stays blocked. The host records this classification on the new
receipt and revalidates its exact task revision, Stop boundary and provenance at
native admission. The worker does not restore a goal, resume the task or remove
dependency/question conditions. It retains ordinary permissions and capacity
checks and charges one saved task turn before submission; an uncertain submission
retains that charge rather than making a retry free.

The recipient may inspect, reply, defer or decline within its existing authority.
Only a separate valid owner decision can resume its objective. Within a standing
delegation, the owning agent may make that decision after verifying that its own
recorded dependency block is resolved; another user approval is not required.
User pause or Stop still requires the user's instruction to resume. Resuming
preserves required limits, used turns, unanswered questions and uncertain effects;
it does not classify those unresolved conditions as successful or authorized.
A human-recorded
block, missing provenance, a block recorded from a paused task, Stop, failure,
completion or cancellation does not receive this exception. Old records can use
their exact retained agent-origin status changes; reason text is never classified
as permission. Notify remains non-waking; steer and automatic outcome notifications
keep their existing admission rules. No old suppressed or uncertain input is
replayed or newly classified.

Before a generic goal re-prompt, a transport-bound root may yield to an already
sealed, qualified saved peer continuation. This is a read-only eligibility check,
not admission: the existing queue still claims durably and rechecks task, Stop,
owner and budget at a real completion/idle boundary. The goal is replaced with an
exact state copy at streaming's host-supersession check; its objective, revision,
turn cap and accumulated progress are retained, and the peer's next generation
can continue it. The boolean live continuation guard is not used for this yield
because rejection clears a goal. Ordinary queues and unknown/submitting receipts
do not trigger it. Native integration tests protect this runtime compatibility
boundary; accepted steering and active jobs retain their normal idle rules.
At admission, the sealed, qualified saved peer result is prioritized ahead of
older ordinary queued peer input to the same chat.

The coordination workflow's locked development environment runs the service
contracts; it does not qualify optional native loops. The release workflow keeps
its ordinary native-runtime selection and separately runs the 35 peer-continuation
and 8 task-goal cases against its resolved Core/live/streaming graph, including the
app's declared dependencies. `scripts/check_goal_peer_junit.py` requires every
expected identity exactly once with zero skips, failures or errors. The workflow
retains that JUnit report and the scripted native ordering traces.
That runtime qualification includes the app dependency closure for test
composition; it does not establish production worker dependency-graph parity.

For `context-managed` with `engine: boundary`, Unified opts into optional durable
checkpoints (an explicit `durable_checkpoints: false` remains respected). The host
adapter writes `context-checkpoint.json` beside control state and uses the
module's checkpoint API. See that module's README for its reusable public
contract. Restore occurs lazily before the first fitted request after provider
settings and the complete native transcript have been restored. Only an exact
covered-prefix hash, compatible model/provider instance, configuration/prompt,
format and record digest can restore a summary. Appended unsummarized messages
remain usable. Rejection/staleness is visible through `task.get`; originals are
never replaced or replayed. The summary is labelled reference history, not fresh
authorization. Older context modules report unsupported.

Before request fitting, the host commits the complete canonical transcript via
the existing native SessionStore and emits hash/index/session/source-revision
references for large tool messages. No second output archive is introduced.
Managed-process results may already contain operation-journal references; this
adapter does not invent them or claim capture of arbitrary past tool bytes.
Provider/tool modules that truncate before returning an output cannot be
recovered by this adapter. Cross-host export and retention policy remain owned
by the existing portability/storage integration.

### Explicit native recovery

`runtime.control` operation `context.recovery.request` requests recovery once
for the current canonical prefix. It requires an idle owner and a stable host
command identity. Optional `targetBytes` chooses a tentative batch size, not a
token allowance. An `armed` receipt means only that the intent was saved: it
does not run a model, submit input, resume a task, or settle an unknown effect.
`context.recovery.status` reports the retained intent outcome without returning
private native windows.

The next independently authorized ordinary request supplies the loop's real
request builder, selected provider, tools and overlays. Before compaction, the
host verifies the source prefix, model/configuration and durable Stop revision,
then records consumption. The context module derives progress separately from
the active checkpoint. It must demonstrate a strictly smaller complete request
that still fits the unchanged hard allowance. Only an exact, confirmed private
checkpoint write permits activation. Required input can remain too large; that
is a refusal, not permission to trim it, increase limits, or switch models.

Overflow never arms recovery automatically. A failed, interrupted or unknown
attempt is not silently re-armed on restart. Installation uncertainty retains
the candidate identity and requires reconciliation; neither a missing response
nor a new input proves that repeating a prior provider call is safe. Canonical
instructions, results and operation identities remain available unchanged.

Validation includes real boundary-module compactions plus host serialization and
restart, corruption/prefix/model rejection, task CAS and idempotency across
restart, a real base-loop evaluator/pause race, and a browser fixture exercising
the actual task controller from UI and agent paths. Browser provider execution is
deterministic; live model quality and physical voice are not claimed.
