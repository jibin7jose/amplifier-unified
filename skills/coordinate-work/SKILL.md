---
name: coordinate-work
description: Commission independent goal-driven workers, review their results, and resolve real blockers through Unified's authenticated coordination controls.
---

# Coordinate work

Use one manager for many independent workers when the objective permits. The
manager chooses priorities, assigns deliverables, reviews results and integrates
accepted changes. Each worker owns implementation, ordinary corrections, focused
isolated validation and a reviewable result or PR. A higher-level manager can
coordinate other managers in the same way. Do not impose a one-worker-at-a-time
pattern or create another status board simply to manage the existing tasks.

## Start with current facts

Use `app_control` to discover only the action prefixes needed for the next
operation. Follow `nextOffset` and, when present, `schemaRead`/`nextSchemaOffset`
to obtain the complete schema. Retain it during this task; do not repeatedly
list every action. Never emulate missing capabilities by editing host databases.

Read `coordination.context` once for the current root and task intent. Follow
`assignment.briefRef` for the current member summary before reading individual
histories. Read an exact result or artifact for a decision; load history only
when it answers a specific unresolved question. A brief or old scratch note is
not current acceptance evidence or new authority.

## Commission useful independent deliverables

1. Break the objective into enough independent tasks to make useful progress in
   parallel. Give each worker the deliverable, acceptance criteria, scope,
   dependencies, existing authority and result destination. Keep genuinely
   sequential dependencies explicit; use real machine/provider capacity and any
   deliberately configured limits, not an invented fixed worker count.
2. Use `coordination.create` for durable independent task roots. Its `text` is
   the complete brief, including acceptance and permission limits; there is no
   separate `acceptance` field. Supply `objective` to save its durable goal;
   optional `constraints` belong to that objective. Creation reserves its result
   notification before the worker runs. Keep the returned request identity
   instead of creating a second worker after a slow or lost response.
3. For a new independent source writer, use `worktree.inspect` with `mode:"clean"`
   and an optional `ref`, then include `execution: {kind:"worktree", expectedCommit}`
   and the relevant `repositoryPath` when commissioning. The host prepares a clean
   committed checkout without scanning unrelated local changes, and saves the objective before the first working turn. `expectedCommit` is the returned full Git commit ID; `sourceRevision` is a different, legacy working-folder snapshot digest. No
   setup-only model turn or manager handoff exchange is needed. The receipt's
   `checkoutId`, `objectiveTaskId` and final `workingDirectory` identify the result.
   Default creation still inherits the manager's folder, useful for read-only
   work; it does not isolate source, ports or test state. Existing workers keep
   the explicit placement/recovery controls in the [reference](references/protocol.md).
4. Let workers complete their assigned objective. Routine repair, cheap preflight
   and focused tests are included; do not require a new disposition after each
   failed check or add fixed attempt/turn/review allowances. Reuse maintained
   project checks and managed test profiles. Reuse passing evidence when the
   relevant inputs match, rerunning affected checks when those inputs change.
   Change approach when repeated failures make no progress.

Use `coordination.send` with `returnNotification:true` for a new follow-up that
needs a result, retaining its stable command ID and exact target. Omitted `mode`
uses ordinary busy-boundary/idle delivery. `notify` is a non-waking note; use it
only when no continuation is needed. Ordinary collaboration within the assigned
intent needs no separate approval. Do not enroll independent chats implicitly.

## Continue when a dependency is resolved

For an agent-recorded dependency block, send the new finding to the existing
owner through ordinary `coordination.send` in auto or queue mode, **without
`resume`**. The host admits this fresh input while keeping the task blocked.
The owner verifies the dependency, then deliberately calls `task.resume` within
its standing scope. No current `humanInput` or fresh human approval is needed.
Do not hunt for a human message ID merely because work is dependency-blocked.

Use the human-backed `resume` recipe in the reference only for an actual stopped,
paused or failed commissioned member. Unknown block provenance, unresolved user
questions and uncertain effects do not become resolved dependencies. Preserve
the same task, used budget, evidence and actual Stop boundaries.

## Return a result or a real blocker

Workers normally report at completion, or when another owner/user must resolve
a concrete blocker. Do not send routine acknowledgements and progress summaries
back and forth. A test waiting for capacity blocks that dependency, not unrelated
implementation. Use the host's saved result notification instead of polling or
adding a second watch for the same work.

Declare one concise `coordination.reply` for the actual delivered `requestId`,
with typed outcome, source/PR identity, affected checks, limitations and evidence
references. Omit `messageIds` unless you know your assistant output IDs from the
current generation. Never use the incoming peer request's `messageId`. The host
seals the declaration against actual root completion; a reply is not independent
review or proof that an unknown effect succeeded.

Retain a local file using `path@sha256:<64 lowercase hex digits>`, relative to
your actual execution folder. For a file containing exactly `hello`, use:

```text
report.txt@sha256:2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824
```

Retention happens before acknowledgement. Verify `artifactVerification: verified`
and each requested file's `artifacts[].status: retained` before claiming preserved
evidence. A plain path or digest in prose does not retain bytes. For failed
retention, later corrections, cross-workspace discovery, adoption, Stop/resume,
shared task claims or unknown outcomes, read the relevant section of the
[protocol reference](references/protocol.md) only when needed.

## Review and close without another worker turn

Read the exact `coordination.result` and needed artifacts. Review the changed
risk against the original acceptance; a fresh broad test census is not required
merely because a different manager received the result. A failed focused check
needs correction, not a new approval to repair it. Send one concrete correction
request to the same owner when necessary; preserve earlier failed evidence.

Use passive `coordination.artifact` for bounded inspection. To consume the actual
file, discover `coordination.artifact.save` and supply your `sessionId`, the exact
`requestId`, `responseRevision` and `reference`, plus a relative destination `path`
inside your current working folder. Its parent must exist. The host streams and
verifies large binary files without credentials or peer-library access. Current
write restrictions still apply; different existing bytes are never overwritten.
Verify returned size/hash and provenance. After an uncertain return, inspect the
destination before another explicit save; saved bytes are not test qualification.

When a completed review unblocks another manager and you continue unrelated
work, send that decision now through `coordination.send` in auto mode. Reference
the exact reviewed source and evidence, optionally linking the incoming request
with `replyToRequestId`. Do not wait for your whole turn to end. This attributed
handoff does not seal a staged result or grant new authority; the recipient still
applies its acceptance criteria and standing scope.

After accepting a member's exact contribution, use `assignment.task.read` then
`assignment.task.complete` with current revisions and completion evidence. The
worker does not need another model turn to acknowledge acceptance or shipment.
Use the available result acknowledgement and membership-retirement controls when
appropriate. Unresolved resource custody remains separate; don't call an incident
resolved merely because its diagnostic task completed.

## Keep shared context small and current

`SCRATCH.md` is a short current context/index, not a second task database or an
append-only journal. Workers keep detail with their tasks. Managers promote only
shared decisions and unresolved dependencies with owners and links. Put durable
conventions in `AGENTS.md`; put useful superseded history outside automatically
loaded context. Do not paste complete receipts, transcripts, release histories
or old directives into scratch, and do not include whole archives automatically.
Reread before editing and preserve other owners' changes and unfinished work.
Scratch text never grants authority, completes a task or settles an unknown effect.

Actual Stop, explicit user limits, permissions, questions requiring user choice,
execution ownership and uncertain prior effects still govern. Read saved receipts
after uncertain responses; never replay mutations to obtain a cleaner result or
retry through another identity. Ask for a missing decision or authority, not for
permission to make an ordinary engineering choice already inside the objective.
