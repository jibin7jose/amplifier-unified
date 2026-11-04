# Task and worker coordination

## Manager and worker operating model

The manager coordinates **many** independent workers, choosing deliverables,
priority and actual dependencies. Each worker receives a complete objective and
acceptance criteria, carries ordinary repairs and focused isolated validation to
completion, then returns a reviewable result or a concrete external blocker.
Routine progress, implementation choices and failed-check repairs do not require
manager acknowledgement or renewed approval. A higher-level manager uses the same
pattern for workstreams. Real Stop, user choices, permissions, explicit limits,
writer ownership and unknown effects still require their proper resolution.

Use current assignment briefs and exact result references for decisions. The
on-demand [coordinate-work skill](../skills/coordinate-work/SKILL.md) describes the
normal loop; its packaged reference retains detailed API and recovery rules.
Host tasks and receipts remain canonical. Shared scratch is only a current
context/index, never another task ledger or source of authority. Historical
qualification sections below are evidence, not instructions to repeat those
checks or add new release gates.

Direction and implementation are distinct: the [workspace vision](workspaces/docs/VISION.md)
sets the one-manager/many-workers goal. The current discovered action schema owns
available commissioning and placement behavior; do not infer atomic placement
from the existence of independent chats. Accepted completion uses the existing
manager task-completion path without another worker model turn.

## Atomic returns for existing-root requests

For new queued work that needs a returned result, `coordination.send` accepts
`returnNotification:true`. The host reserves the requester's return capacity
and commits its saved wait with the attributed recipient input and request
receipt before scheduling admission. Unsupported adapters or insufficient
capacity refuse before these effects. Pre-commit failures restore affected
database and in-memory state; a committed request survives a lost acknowledgement.
An exact retry returns its original receipt and never rearms work.

Omitting the option, or passing false, preserves ordinary one-way sending.
True with notify or steer is refused. Notify remains non-waking. Creation keeps
its existing default automatic return. Explicit `coordination.subscribe` remains
available for a request sent without a return policy; no old request is migrated,
replayed or enrolled in an assignment by this addition.

Send receipts and exact result reads expose a bounded `returnState` projection
of the existing records. Subscription, staged/sealed response, terminal
qualification, notification delivery, explicit acknowledgement and artifact
retention are separate facts. A claimed wait is not proof of notification
delivery. Queue acceptance is not model consumption; terminal qualification is
not independent artifact verification or review approval. Negative reports can
return without satisfying a successful dependency. Unknown evidence remains
unknown and is never a retry instruction. A timed-out peer control retains its
original input correlation until its worker replies or exits. An exact late
acceptance or explicit no-effect refusal reconciles that saved receipt; neither
resends the request. A delayed native steering event may likewise settle the
original input/generation while its Stop binding remains current. A recovered
result or acknowledgement in assistant prose is not delivery evidence. Missing
native evidence remains unknown, including after process loss.

Saved waits retain task/Stop/location and capacity checks at admission. A busy
requester waits for a real idle boundary. Backpressure, changed task authority,
Stop or an uncertain admission remain explicit and do not trigger replay or
budget reset. An earlier reviewed response never accepts a later candidate.

Qualification must exercise the public request/reply path across two existing
independent roots, including a second correction round without separate
subscription calls or manual completion messages. Scripted runtime evidence,
real Worker-process evidence and actual-model cooperation remain distinct.

## Cross-workspace existing-root communication

The October 26 user direction is: “Lets spin up a task that lets you do so in
the future. We need to have cross-workspace chat support.” This section records
the proposed implementation of that direction, not completed qualification.
It extends the historical same-workspace choice below for communication between
existing roots in the authenticated user's Unified installation.

- Discover exact root identities with `coordination.list`, using `scope:"all"`
  explicitly for other registered workspaces, including managed-location chats.
  Default discovery remains workspace-scoped. Address the returned `sessionId`,
  not a title or a guessed path.
- Use ordinary `coordination.send`, `reply`, `result` and request-scoped retained
  artifacts. The host binds both chat identities and locations to the request.
  Each chat keeps its own configuration, execution folder, permissions, Stop
  state and budget. Sending does not commission, enroll, reparent or resume an
  independent recipient. Notify never wakes; unsupported steering stays explicit.
- Message attribution identifies both locations. A managed chat is labelled as
  managed storage, not registered as a workspace. This is not cross-user or
  remote-host federation and provides no new credentials or filesystem access.
- Explicit references remain citations unless the existing exact-file result
  mechanism retains their bytes. General peer output libraries, private drafts
  and files are not shared by sending. Request participants can inspect their
  exact retained result relationship. A registered Full-access observer may also
  read that exact request and its declared retained artifacts, using `scope:"all"`
  for other workspaces. It remains an observer: original participants, producer,
  response provenance and notification ownership are unchanged. Saved endpoint
  location checks still apply; a result read never loads transcript history.
- Stable request IDs, cancellation and unknown receipts retain their original
  meaning. Shipping this change never resends rejected or uncertain old input.

Focused synthetic action-path and rendered checks must qualify the candidate.
Native-provider behavior is not established by deterministic transport fixtures.

**Adapter limit:** a sender with an assignment token or cost cap cannot queue
or steer work in an independent root, because this adapter cannot enforce that
cap on a single peer request. The refusal is explicit. Informational `notify`
remains available without waking the recipient. Communication never removes a
cap or enrolls the recipient to make a refused request pass.

## Compact orientation and delegated recovery

Fresh direct queue messages can be handled by an independently dependency-blocked
root when its canonical task proves that its own agent recorded the block from an
active objective. This is one receipt-bound conversational input, not task resume.
The task, dependencies and automatic goal remain blocked; the recipient chooses a
valid next decision under its own authority. The message remains peer-attributed.
Exact task/Stop revisions, saved turn/cost/token limits, configuration and native
ownership checks still apply. Explicit Stop, paused/completed/cancelled states,
human-recorded blocks and unknown block provenance remain refusals. Old suppressed
receipts and automatic result notifications do not acquire the new exception.

The current coordination reference offers `assignment.briefRef` to an actual
root assignment member. Following it reads the existing bounded, paged member
brief. The reference does not load a roster or peer histories at every model
call, accumulate another history message, or replace exact constraints and
Shared Work evidence. Internal children do not receive a root-only action hint.

A refused child/root identity mismatch directs the child to return the requested
action, completed effects and refusal to its delegating parent. It does not lend
root authority, retry an operation, or require the human to relay an already
authorized step. The root must independently satisfy current Stop, scope,
permission and uncertainty checks before acting.

`effectAuthorized:false` on a read receipt means the read grants no authority.
That field alone is not an execution denial: preserve the receipt's status,
coverage and reason, and obtain the ordinary action's current admission result.
This guidance does not weaken the required-detail or effect admission checks.

## PR integration status

The grant-free repair is being integrated onto the `0.20.81` mainline at
`8b24a838`. This preserves that mainline's scoped state publication, source-URI
handling, composer controls and runtime error reporting. Peer lifecycle events
must persist both affected receipt-reference owners, without checkpointing
unrelated chats. The final PR records checks against its exact integrated head;
the earlier evidence below is not automatically evidence for that newer head.

The retained repair candidate `6d6818e2` passed 845 selected Python tests,
456 frontend tests, reproducible assets and two rendered browser runs. In an
isolated production-qualified environment, two actual-model task chats produced
two rounds of artifacts with four qualified replies and automatic continuations.
The parent reached its explicit cumulative token threshold before final wrap-up.
A separately authorized, bounded **new follow-up in those same chats**, not a
replayed input or repeated workflow, then wrote/read the final manifest and
finished successfully. Exact native/web identity and three-role effective-plan
checks passed, as did separate notify, ACK, idle-steer and streaming-Stop checks.
The original budget-stopped run remains recorded as failed.

These checks used full Work plus 21 behaviors, selected/effective OpenAI/Astra
with xhigh effort, an official provider endpoint and the user's long-context
configuration. External service/device targets were isolated. Earlier relay
tests omitted that long-context setting and failed; a newer context dependency
also resolved for the normal-route run. This is not a causal comparison proving
which difference removed the context failure. No provider patch was made.
Per-call wire effort, physical HTTP count and usage for the intentionally stopped
call remain unknown. The stopped chat's ordinary unknown-usage policy paused it.

This is scoped verification, not every-provider or full restart/crash-matrix
certification. The real-model evidence belongs to `6d6818e2`; integration checks
must be reported separately. The old test infrastructure was removed after
source/evidence preservation. No live-service installation or release is implied.

## Model-boundary recovery — 2026-10-07

Agent `coordination.*` dispatch returns keep full protocol receipts, effects and
result evidence, but replace the unrelated repeated app overview with revision,
calling-session identity and explicit `get_state` guidance. UI dispatch and other
agent action state contracts stay unchanged; legitimate large coordination
payloads are not truncated. Exact-message reads preserve the saved web `id` and
expose verified native alias/index/generation linkage when available, never an
alias inferred from equal text or an arbitrary requested ID.

The parent settled **same-workspace collaboration** after the closeout below.
No new grant, family restriction or budget change is part of this repair.
The subsequent actual-model trial failed during local context preparation after
one sealed artifact result and a committed second root; it is not a pass.
Reply-byte savings are measured separately from token estimates and cannot prove
that the full model workflow now fits. Parent owns the corrected real-model trial
and independent acceptance. Prior reports and failed outcomes remain unchanged.

## Grant-free candidate — 2026-10-07

This candidate replaces the separate collaboration approval/commissioning
ceremony. It is **not release or full native-model acceptance**. The earlier
qualification sections below describe their original revisions, not this change.

1. **Authority and inheritance.** Fresh `coordination.create`, `send` and
   `subscribe` require an authenticated actual root/current generation or a direct
   authenticated human action. Peer-woken generations need no new human message.
   Targets remain ordinary roots in the same workspace. Raw agent `session.create`
   is refused: the host owns configuration/bundle/provider/selection inheritance.
   Children cannot borrow the root identity; peer stop, worker control and settings
   mutations remain refused. Existing file/tool/destructive/budget controls remain.
   The workspace check is a mechanical boundary, not proof of semantic task
   relevance. Purpose is instruction to the model. The user selected
   **same-workspace**, including eligible existing roots; it is not restricted
   to the commissioning family.
2. **Durable receipts.** Create and queue send commit stable request/input/root IDs
   before returning, then continue under service task ownership. Identical retries
   only read receipts. Runtime qualification emits elapsed progress and is bounded
   by a separate 3,600-second preparation timeout. Worker readiness retains its
   600-second bound after preparation. Stop cancels pre-worker preparation and prevents input
   admission. Only a positively observed pre-admission failure becomes `not_sent`.
   Post-write/restart uncertainty remains `unknown`, never automatically replayed.
   Creation uses the global **Outstanding task chats per manager** ceiling
   (default eight) in Settings → Testing & resources → Advanced execution limits.
   It counts outstanding tasks per source, not completed history. Changing that
   setting affects new creation admission and never stops existing children.
   Outstanding means actual queued/preparing/running work, pending creation or an
   active saved task. An idle finished root without a task, or an unknown historical
   creation receipt with no outstanding root work, does not permanently consume a
   slot. Unknown evidence is neither resolved nor replayed by that count.
3. **Results and related work.** Exact reply/generation/input/terminal checks and
   one guarded continuation remain. Notify never wakes; unsupported steering is
   explicit. Related work is read-only: no grant, proposal, peer-message or
   commissioning forms. Original-message inspection and same-workspace artifact
   opening retain the caller's selection and unsent draft. A sealed declaration
   still is not independent artifact verification.
4. **Upgrade.** Grant/decide/revoke return explicit retired (410) errors, including
   the old approval-response alias. Legacy queued/pending work and saved waits are
   suppressed before any drain; submitting outcomes become unknown. Historical
   grants, denials, revocations, messages and result links remain readable. Old
   grant-bearing fresh writes are unsupported, not silently upgraded or authorized.
5. **Sibling qualification reuse.** The first exact inherited effective plan may
   require qualification in a private profile for its app generation. Identical
   siblings coalesce under the existing profile lock and later reuse that qualified
   identity. Local build-input signatures cover module/bundle source declarations,
   not tool write policies, cache paths or context documents; writing task artifacts
   does not invalidate an otherwise identical plan. Full edited-plan hashing stays
   conservative, and actual local source changes require requalification. Effective
   execution configuration and ordinary provider credential rebinding are separate
   from Python graph reuse. Immediate reuse of the parent's offered-profile
   descriptor remains deferred, not claimed.

Candidate checks use retained exact-commit interpreters in the assigned DTU.
The new real-`RuntimeManager` slow-ensure tests use a deterministic child process,
not a native Worker/model; browser fixtures use emulated terminal anchors. The
scripted native probe uses actual native sessions with a direct event pump,
not full Worker-process ownership. These evidence classes must remain separate.

The earlier report over-constrained sibling reuse by treating model/credential
identity as a prerequisite for every Python graph reuse. That report remains
historical evidence, not the current engineering requirement. No full provenance
subsystem or parent-profile equivalence shortcut is introduced.

Session-local module source selectors are copied narrowly through ordinary session
settings with their precedence, included in the inherited configuration hash.
The full effective plan and selection/configurator/mode/budget/capacity controls
remain; task/goal/history and credential configs are not copied into the source
settings snapshot. An incompatible destination source or lazy-only override that
the installed Foundation cannot resolve is explicitly refused before a different
source can execute. Default prepared sources need no new stamp.

Failed/cancelled qualification attempts retain `profile-attempt.json` and their
private staging artifacts, without validated/index admission. Cancellation may
leave an already-running filesystem thread; the environment owner must establish
that writers stopped before removing those paths. No cleanup of uncertain shared
projects or automatic retry is implied. Outer drain cancellation persists definite
pre-admission `not_sent` and propagates cancellation without touching the next input.
Normal `ready` to `idle` warmth schedules only the existing guarded queue drain;
warmup alone never creates a task input.

### Observed checkpoints (not complete acceptance)

- `3a32ab77`: the first gate suite passed 11 checks and failed one incomplete
  historical fixture. The seven-file regression run passed 197 and failed two
  obsolete raw-creation error expectations. Later fixture repairs retain those
  failed logs rather than rewriting them.
- `8658372d`: 410 Python checks and all 456 frontend checks passed, with zero
  skips; production build passed. The browser check stopped at an obsolete
  related-task label that omitted the newly displayed status.
- `f0452aa6`: 473 focused/regression Python checks passed, zero skips. The
  scripted native probe passed with two real native roots, zero coordination
  approval calls, request-boundary steering, exact native result lookup and one
  continuation. It made zero network/model calls. The browser completed two
  artifact rounds per root and then failed because its post-reload preservation
  assertion ran before hydration. A subsequent `2a3b8d50` run established that
  reload restores Chat details rather than its local nested Tasks panel. The
  test now reopens that read-only panel and waits for the real heading/request
  row, not an arbitrary delay. That revision also passed 473 Python and 456
  frontend checks and reproduced the committed production assets.

Final exact-commit commands, XML/TAP, artifact hashes and acceptance dispositions
are retained in the implementing lane's `.ci/repair-evidence/`, with a final
manifest at `.ci/repair-evidence/final.json`. No historical checkpoint above is
promoted to a pass for later code. The closeout corrective pass is recorded
separately in `.ci/repair-evidence/closeout-resume.json`. Full default-profile
Worker/native/real-model qualification, independent artifact checks, Fable
delta/outcome review and the final scope decision remain parent-owned.

## PR qualification — 2026-10-06

The same-host collaboration increment is qualified for PR review, not released
or deployed. Integrated source `8003efaf9ffe9ab04268440966bfc1fd30ee6e09`
combines the reviewed implementation with upstream v0.20.51
(`bbc070235fd1d85a5c6835b28ecb1be697aabd51`). Commit
`c9c5e3af26c5fb30ca380930b72104306163e4fa` changes only the generated static
assets to the verified combined build. Later documentation does not change the
qualified code or assets. Historical failures and narrower checks below remain
attributed to their original revisions.

| Boundary | Observed result |
|---|---|
| Integrated Python, one source and qualification interpreter | 5,081 unique passes, 111 explicit skips, four passing subtests, no failures/errors. The full groups produced 5,071 passes and 121 skips; ten of those skipped native-runtime cases subsequently passed with the same interpreter explicitly selected. They are not double-counted. |
| Frontend and packaged build | 440 passes; v0.20.51, build ID `c6d0dceb4b5f6b56`. On `c9c5e3af`, `npm ci`, production build, build-artifact test and `git diff --exit-code -- amplifier_web/static` all passed; all 95 asset hashes match. |
| Production-service browser and native scripted runtime | Complete deterministic coordination and startup browser flows passed. The native scripted probe checked reply, steering and continuation without claiming model efficacy. |
| Actual installed worker and model | On `0bfd83f1`, three Fable roots performed two checked artifact rounds (77 then 88, retaining the first), created and executed a durable task with the same credential binding, applied an active-generation correction, and admitted one continuation per result. A separate real-model proposal remained approvable after 65.7 seconds and an application restart. All 27 calls completed; effort was default/unset. This is evidence for that pin and the subsequently reviewed unchanged components, not a new live-model run on the integrated source. |
| Retained native result to rendered transcript | On `3b4e188d`, a real Chromium click resolved a retained native result to its original web UUID, focused an article inside the messages pane, preserved nine rows in order and retained the caller's draft. Seven service-level cases separately checked aliases, cold paging, sparse order, repeated reveal and ambiguity refusal. No model work was replayed. |

All execution checks above ran in the owned DTU. Canonical row references and
original fixture hashes were independently checked. The rendered-reveal harness
retains an exit-1 result from an additional configuration-preview assertion
after the focus checks passed; the no-generation guard refused startup before
a worker existed. Draft retention was separately confirmed from saved state.
This is not reported as a wholly successful harness run.

### Independent review and remaining limits

Fable (`claude-fable-5-1`) reviewed the design, implementation, test outcomes,
repair deltas and upstream integration read-only. Blocking findings were
corrected and rechecked. The last code review found no blocking defect in the
native-to-web reveal repair; integration review required the exact asset rebuild
and a pristine-upstream comparison of the optional new-chat smoke. Both
conditions were subsequently verified.

The additional `test:new-chat-browser` smoke fails at line 22 because it expects
`.a-canvas-toggle` before a chat exists. One run on pristine upstream
`bbc07023` reproduced the same failure and the same rendered accessibility
snapshot (normalizing only the temporary fixture directory). It is a confirmed
pre-existing optional test-contract gap. Assertions after that line remain
unreached, not passed. The test and product UI were not changed to conceal it.

The 111 remaining skips cover explicit optional modules and unconfigured
integration probes, including 72 optional apply-patch writer checks; skips are
not evidence of protection or interoperability. No universal checkout lock,
cross-host collaboration, production deployment, provider-native mid-request
cancellation or all-provider parity is claimed.

Non-blocking review follow-ups remain: classify known pre-admission startup
failures more precisely than conservative `unknown`; validate unusable
creation permission combinations before proposing human approval; improve
late-proposal attention and cold-history reveal efficiency. Non-provider
literal tool/hook credentials are deliberately omitted from inherited task
snapshots, not implicitly rebound; environment references are preserved.
Their modules may therefore need separately configured credentials.
Unknown submissions are not automatically retried.

Qualification artifacts are retained by the implementing workspace under
`working-files/collaborative-dtu/`: `integrated-8003/evidence/qualification-summary.json`,
`final-prepr-c9c5/evidence/{commands,baseline-comparison}.json`,
`live-acceptance-0bfd/evidence/completion.json`, and
`reveal-3b4e/evidence/final-report-v2.json`. These locations are evidence pointers,
not runtime dependencies. The PR carries this self-contained result and the
earlier committed manifests; local credential/configuration files are not
publication artifacts.

## Historical grant-based increment: admission specification

This scoped implementation follows the approved Canvas v4 direction recorded in
`workspaces/contracts/collaborative-workspaces-decision.md`. Accepted execution
contracts remain unchanged. Shared folders are cooperative, not protected by a
checkout-wide lease.

1. **Authority.** A host-owned grant records `id`, `sourceMessageId`, issuer,
   workspace, participant root IDs, purpose, allowed modes, idle-start and
   task-creation permission, revision and revocation. Direct authenticated human
   actions remain available. An agent may propose a grant anchored to its actual
   current delivered human `sourceMessageId`; the host validates transport-bound
   root/generation and retained human provenance, then requests one approval of
   the exact scope through the existing approval surface. It does not synthesize
   human speech or infer authorization from keywords. Only a human may revoke.
   Membership and model-supplied flags grant nothing.
   Equivalent legacy send/create/worker routes must pass the same gate.
   Children retain their actual runtime identity and cannot borrow root authority.
   Collaboration never permits peer stop or settings/permission changes.
   Pending proposals survive the transient approval timeout and host restart in
   existing command receipts. `coordination.context.proposals` exposes their
   stable proposal IDs; Related work and `coordination.decide` allow a human to
   decide later without a live original generation or new user text. Approval
   rechecks the retained authenticated human source/digest, exact scope, roots,
   workspace and interruption revision. Explicit denial cannot be revived.
2. **Attributed input.** Store the original text once in the existing target
   message history with `inputOrigin=peer` and a host envelope: sender root/native
   identity, recipient, grant/revision, request/input ID, mode, purpose,
   references and optional reply linkage. The worker renders that immutable
   envelope around the text before model admission. Peer content is scoped task
   input/reference, never a new human instruction or authority.
3. **Delivery.** Notify persists without execution. Queue waits for a real idle
   boundary; ordinary busy `conversation.send` is not a queue adapter. Steer is
   supported only through generation-anchored runtime admission; unsupported is
   explicit with no message or execution side effect. At admission recheck the
   current grant, stop/task revision, budget and native owner. Unknown receipts
   are never replayed. Human control wins over a queued message.
4. **Reply qualification.** A recipient explicitly declares `coordination.reply`
   for the exact delivered request during its actual root generation, with
   `kind=result|ack|defer|decline`, outcome, text and artifact/message references.
   The host stages that declaration and seals it only on matching successful
   root termination, delivered (not merely accepted) input IDs, no active jobs,
   and an exact checkpoint-derived native assistant anchor without tools.
   No provider final channel is invented and no prose is classified.
   Commentary, acknowledgement/progress, unrelated turns, failure, defer and
   decline cannot satisfy it. Message IDs remain the result identity; result
   references are not proof of independent artifact verification.
   Multiple inputs delivered in one generation may have separately typed replies
   sharing the same exact terminal message; this does not imply separate answers.
   Restart changes unresolved staged replies to unknown with dependency attention,
   leaving the continuation unclaimed. Already sealed evidence is preserved.
5. **Wait and continuation.** Cursors only acknowledge results. A saved wait
   binds one request and one saved task revision, interruption revision and grant.
   One qualifying response may admit one stable-ID continuation through existing
   worker input admission. Busy senders wait for idle; paused/stopped/revoked or
   exhausted senders stay stopped. Unknown/crashed admission is retained without
   resend. Unsupported runtime qualification/admission is a blocked acceptance
   area, not successful completion.
   Creation and subscriptions require both queue mode and explicit idle-start
   permission before any effects; a notify-only grant does not commission work.

Discovery filters root summaries before paging. Cold reads target one native
history, return bounded relevant windows and report unavailable history rather
than treating an unloaded projection as empty. Task chats are ordinary roots
with explicit title, creator/request links, same workspace, output namespace and
recorded configuration snapshot. No second transcript, database or scheduler.
Related work and agent actions share this service and preserve selection/drafts.
The on-demand `coordinate-work` skill must be installed and discoverable.

Implementation and validation status is recorded below only after observed
checks. This specification does not assert that the new capabilities exist yet.

## Fable findings continuation: bounded fixes checked; parent gates pending

Final tested implementation and assets:
`3b4e188dd03c88f88067d1928c06411ae0625538`. This is the same objective,
worktree and branch, with all prior returns, failures and commits retained.
Subsequent evidence-only edits do not change that code, tests, skill or assets.
No locked v1 direction changed.

### Corrections and discriminating boundaries

1. **Usable grants.** Creation and subscriptions fail before effects without
   queue plus explicit idle-start authority and a guarded-input adapter.
   Notify-only grants do not create silent unstartable tasks or stranded waits.
   Tests cover all permission/mode combinations; the browser couples creation
   permission to idle starts and disables commissioning without prerequisites.
2. **Durable proposals.** The existing command receipt retains proposal ID,
   authenticated human source/digest, exact scope and interruption revision.
   Expiration is pending, not denied. Later human decisions through Related work,
   `coordination.decide` or the existing approval action require no new chat text
   or original live generation. Restart/late approval, scope/source edits,
   foreign/model decisions, explicit denial, once-only approval and revocation
   are checked. No scheduler or second history store was introduced.
3. **Restart and participant safety.** Unresolved staged replies become unknown
   with dependency attention and no claimed/enqueued continuation. Preserved
   generations lose live authority on restart; sealed evidence is untouched.
   Model result reads require the exact request participant, authorized root and
   workspace before reading target history. Revocation suppresses execution but
   does not erase scoped historical reads.
4. **Truthful presentation.** Discovery advertises steer only for a granted
   currently valid target adapter/generation. Related work shows actual result
   status, declaration, canonical terminal and artifact links; the browser
   reveals and checks the terminal's rendered text, not a constant disclaimer.
   Creators use readable titles, with IDs in details. Exact native presentation
   reads do not use visible paging offsets as native indexes or borrow grant
   authority. Verified aliases preserve web UUIDs and order, including live
   bubbles without native indexes; ambiguous aliases refuse rather than match
   equal prose.
5. **Explicit operation policy.** Shared host-owned action metadata replaces
   suffix-based peer permission. Controlled routes bind the caller, including
   implicit routing/module/source configuration and browser call startup.
   Domain-owned process, observation, native-capture, publishing and recovery
   checks retain their original authority/diagnostics. Unknown targeted mutations,
   equivalent execution/settings/worker bypasses and malformed targets refuse.
   The eight previously reported broad regressions are now checked successfully;
   tool-delegate was repaired only in the dedicated DTU qualification environment.

### Final-code checks

All builds/tests/install ran inside the existing parent-owned
`collaborative-e1667af2`; no additional environment was launched. The check
interpreter is `/opt/collaborative-dtu/fable-qualification/bin/python`, Python
3.13.16. `PYTHONPATH` is explicitly unset. Actual imports and pinned installed
distributions are retained in
[`workspaces/evidence/collaboration-fable-runtime.json`](workspaces/evidence/collaboration-fable-runtime.json).
Foundation/tool-delegate use `4e60f6699d71b7c53bc24bce8efceef64207fee1`;
Core remains 2.0.1. No host syntax compilation was used.

| Check | Observed final-code result | Evidence boundary |
|---|---|---|
| Focused collaboration, presentation and transport | **265 passed, zero skipped** | Restart/late approval, grant combinations, participant access, canonical aliases and adapter safety. |
| Complete five-area Python run | **5,041 passed, 111 skipped, zero failed; 4 subtests passed** | All five groups used the exact same SHA. Every one of 5,149 collected nodes matched a terminal JUnit record. |
| Frontend unit/build | **440 passed, zero skipped; build PASS** | Rebuild reproduced all 95 committed asset hashes. |
| Production-service Chromium loop | **PASS, completeLoop true** | Delayed durable approval after transient expiry/reload, task creation, active steering, typed result, rendered exact terminal, one continuation and adjacent exchange. Deterministic adapter, not real-model evidence. |
| Native scripted probe and installed wheel | **PASS** | Two Core/native roots, actual app-control tools, steering and one continuation; wheel imports 232 exact Python files, all 95 assets and the durable-proposal skill. Scripted providers make no model/network calls. |

The full split command, in the final checkout, was:

```sh
Q=/opt/collaborative-dtu/fable-qualification/bin/python
for area in a-f g-m n-q r-s t-z; do
  env -u PYTHONPATH UNIFIED_RUNTIME_PYTHON="$Q" timeout 800 "$Q" -m pytest \
    @/opt/collaborative-dtu/fable-$area-3b4e.args -q --tb=short -ra \
    --junitxml=/opt/collaborative-dtu/fable-$area-3b4e.xml
done
```

Each group's log and exit code were retained separately, with five zero exits.
The accounting is 5,041 passes plus 108 execution skips = 5,149 collected nodes.
Three collection-skip records produce 111 reported skips and 5,152 JUnit records;
the four subtests are reported separately. Nothing unobserved is counted as a pass.

The exact results, commands, skip reasons, failed earlier checkpoints and log/JUnit
hashes are in
[`workspaces/evidence/collaboration-fable-results.json`](workspaces/evidence/collaboration-fable-results.json).
Asset provenance is in
[`workspaces/evidence/collaboration-fable-assets.json`](workspaces/evidence/collaboration-fable-assets.json).
DTU logs use `/opt/collaborative-dtu/fable-*-3b4e.*`. Final logs/JUnit, collection,
split manifest, environment-install/pins and wheel checks were also pulled to the
lane's ignored `.ci/fable-evidence/` so the raw evidence is not dependent solely
on the live DTU. Earlier `6907`, `0bfd`, `3f43`, `1876` and `gaps-*` evidence is
retained under its original source identity, not overlaid into this pass.

### Remaining gates and ownership

The 111 skips remain explicit: 72 optional writer-module checks lack
`amplifier_module_tool_apply_patch`; others require optional OpenAI/native,
context-managed, profiling, CLI/Python Playwright, CI/contribution, history-edit,
recipe or warm-worker setup. The proposed standalone apply-patch source lookup
could not authenticate/resolve; no credential workaround or retry was attempted.
These are not relabelled product failures or positive native proof.

**Parent still owns the installed actual Fable created-task/adjacent-round/
live-steering check on this final code, final Fable delta review and PR decision.**
The prior actual-model `b0dccb43` result remains valid historical evidence, not
qualification of later code. The bounded static follow-up passes the last reveal
correction only; it is not Fable's final review.

The parent retains DTU/Gitea teardown. No lane qualification process remains.
The pre-existing parent fixture PID 48965 was left untouched. No GitHub push, PR,
merge, deployment, resource teardown or external DONE-marker attempt was made;
only authorized private-Gitea source snapshots were mirrored for DTU staging.
The host shared uv environment has no `/tmp/` editable-path references.

## Historical live-acceptance gap continuation: fixes checked; qualification BLOCKED then

This continues the same lane, worktree and objective. Earlier attempts, failures,
commits and the passing independent model result are preserved. The final tested
implementation is `91e22bfe29ced4d8cd1e75220316b26714fad8f4`; any subsequent
evidence-only commit does not change its code, skill or compiled assets.
The terminal outcome is **BLOCKED for full-suite/live acceptance**, not a complete
feature claim. No final Fable review was requested from this lane.

### Three bounded fixes

1. **Credential rebinding.** Inherited task snapshots retain explicit environment
   references or a host-credential marker, never an expanded provider secret.
   Preparation resolves markers only against the same host-composed provider
   module, instance, source and nested-agent scope. Missing, disabled, ambiguous
   or changed bindings refuse explicitly; required-secret schema validation
   remains strict. No provider-family environment variable or alternate account
   is guessed. Bound workers persist reference-only effective snapshots too.
   Tests exercise the actual `prepare_manager` path, schema materialization,
   exact default/priority, pinned model/effort and multi-instance keys. Module
   mounting in those tests is mocked: this is **not** proof of a newly created
   real-model worker starting successfully.
2. **Native bridge metadata.** The service consumes only `_generationId`,
   `_runtimeSessionId`, `_inputBindings` and `_inputClients` before validating
   public operation arguments. Trusted principal/generation/provenance remain
   in their context bindings; browser origin and context-specific metadata stay
   intact. Actual `Worker.app_access_bridge` tests cover history list/search/read,
   forged transport replacement, arbitrary-extra rejection, observations,
   child identity and context restoration. Public history schemas were not
   relaxed.
3. **Scope-specific compatibility.** Nullable draft targets reach the existing
   Canvas guard; empty executable identities still refuse. Exact organization,
   history, presentation, readiness and deletion-preview actions retain their
   existing downstream checks. Message presentation/annotations retain the
   shared caller guard. Name regeneration, peer execution/control/settings and
   actual deletion are not made passive. Older fixtures now carry real caller
   provenance; unauthorized model creation remains denied while human creation
   and ordinary default/session inheritance are still tested.

The genuine pre-feature baseline is
`2e310274d3e401200d930c9e7a7e900563eaeaa1`. `4932263b` is this lane's first
implementation, not an upstream baseline. The null Canvas identity regression
was introduced by this feature's generic guard; reproducing it on `4932263b`
never established a pre-existing upstream defect.

### Checks after the last implementation change

All execution below was inside the existing parent-owned
`collaborative-e1667af2`; no new DTU or model acceptance run was created.
The checkout's Python 3.13 environment reused the installed native runtime via:

```sh
cd /workspace/checkouts/91e22bfe29ced4d8cd1e75220316b26714fad8f4
export PYTHONPATH="$PWD:/opt/collaborative-live-readiness/runtime/.venv/lib/python3.13/site-packages"
export UNIFIED_RUNTIME_PYTHON="$PWD/.venv/bin/python"
.venv/bin/python -m pytest -q \
  tests/test_provider_environment.py tests/test_runtime_controls.py \
  tests/test_runtime_worker.py tests/test_history_query.py \
  tests/test_collaborative_workspaces.py tests/test_canvas_draft.py \
  tests/test_browser_state.py tests/test_input_origin_browser.py \
  tests/test_observation_input.py tests/test_host_session_resume.py \
  tests/test_coordination.py tests/test_runtime.py tests/test_collaboration_input.py \
  tests/test_agent_chat_controls.py tests/test_agent_canvas_scope.py \
  tests/test_conversation_library.py tests/test_naming_controls.py \
  tests/test_canvas_visibility.py tests/test_attachments.py tests/test_client_layout.py \
  tests/test_builtin_behaviors.py tests/test_standalone.py \
  tests/test_creation_inheritance.py tests/test_desktop_readiness.py \
  tests/test_headless.py tests/test_managed_chats.py tests/test_managed_deletion.py \
  tests/test_message_delivery.py tests/test_message_interactions.py --tb=short -ra \
  --junitxml=/opt/collaborative-dtu/gaps-relevant-91e22bfe.xml
```

Observed results:

| Check | Result | Boundary |
|---|---|---|
| Final relevant Python suite | **616 passed, zero skipped**, 265.02 seconds | Includes all changed-test areas and inheritance/browser/draft/authority compatibility. |
| `npm test --prefix frontend` | **440 passed, zero skipped** | Final implementation source. |
| Production build and asset hashes | **PASS**, all 95 files identical | Rebuilt the exact final source; tracked outputs match the DTU manifest byte-for-byte. |
| `npm run test:coordination-browser --prefix frontend` | **PASS, completeLoop true** | Actual production service/Chromium; deterministic runtime and emulated terminal anchor, not live-model proof. |
| Native scripted-provider probe and wheel installation | **PASS** | Two native roots, actual tools, steering and one continuation; installed wheel has accurate typed-reply/create skill and all 95 matching assets. No real-model efficacy/startup claim. |

Browser commands also set
`AMPLIFIER_TEST_ROOT="$PWD"`, `AMPLIFIER_TEST_PYTHON="$PWD/.venv/bin/python"`
and `NODE_OPTIONS=--require=/opt/collaborative-dtu/chromium-env.cjs`.
The native command was
`.venv/bin/python tests/fixtures/collaboration_native_probe.py`.
Wheel commands were `uv build --wheel --out-dir /opt/collaborative-dtu/gaps-wheel-91e22bfe`
and `uv pip install --python .venv/bin/python --no-deps --target
/opt/collaborative-dtu/gaps-wheel-91e22bfe/installed
/opt/collaborative-dtu/gaps-wheel-91e22bfe/*.whl`.
Wheel import/resource checks used that installed target, not the source tree.

Assets were first generated from `019bd6e9681e332e5da2fa75eacadb37b3f167d3`,
transferred through `amplifier-digital-twin file-pull`, and committed with obsolete
hashed chunks removed. Rebuilds on `a72ce6b8`, `3940fc28` and final `91e22bfe`
reproduced every hash without tracked changes. The source/hash manifest is
[`workspaces/evidence/collaboration-gaps-assets.json`](workspaces/evidence/collaboration-gaps-assets.json).
The skill names exact `coordination.create` fields: acceptance criteria belong
in `text`, not an invented `acceptance` argument.

### Broad-suite accounting and exact blockers

The monolithic `pytest -q tests --tb=short -ra --junitxml=...` run on `019bd6e9`
was interrupted by the DTU CLI's **600-second JSON-exec timeout**, before the
requested 1500-second pytest bound. Its output/JUnit are retained, not relabelled
as complete. Of 5,110 collected nodes, 2,278 completed before that transport
timeout. The remaining 2,832 were declared in three areas and executed through
`exec --stream --timeout 930` with a 900-second pytest bound:

| Area | Source | Observed result |
|---|---|---|
| n–q, `@/opt/collaborative-dtu/gaps-remaining-n-q.args` | `6782ec89` | 942 passed, 4 failed, 12 skipped |
| t–z, `@/opt/collaborative-dtu/gaps-remaining-t-z.args` | `6782ec89` | 844 passed, 1 failed, 73 skipped |
| r–s, `@/opt/collaborative-dtu/gaps-remaining-r-s-files.args` | `91e22bfe` | 950 passed, 2 failed, 4 skipped |

The first r–s node-ID invocation failed before execution because the installed
Core pytest plugin called `Path.exists()` on an overlong parametrized node ID.
Its INTERNALERROR is retained. Changing to 69 ordinary file paths completed
the area without disabling the plugin, deleting tests or changing product code.

After overlaying the final relevant checks, the mixed-source accounting is
**4,992 passed, 8 failed, 110 skipped, zero unobserved collected nodes**.
This is **not a single-SHA full-suite pass**. Two modules skipped collection
(`test_browser_auth`, `test_context_continuity`) also remain explicit; their
uncollected test count is unknown. The structured record contains every run's
source, counts, skip reasons, remaining failure names and log hashes:
[`workspaces/evidence/collaboration-gaps-results.json`](workspaces/evidence/collaboration-gaps-results.json).

Remaining blockers, not waived:

| Tests | Exact cause / disposition |
|---|---|
| `test_host_children.py::test_real_foundation_child_lifecycle_and_delegate_contract` | Missing `amplifier_module_tool_delegate` in the selected check environment. Reproduced on genuine baseline `2e310274`; this proves an environment limitation, not a green product check. |
| `test_observation_presentation.py::test_exact_retry_cannot_upgrade_presentation_and_agent_cannot_choose_client`; `test_observations.py::test_operator_boundary_human_provenance_and_cross_session_scope` | Generic collaboration denial preempts the expected trusted-input/human-request diagnostics. Refusal remains, but scope-specific regression checks fail. Not claimed pre-existing. |
| `test_operations.py::test_operation_cancel_uses_existing_hooks_and_rejects_redirect`; `test_operations.py::test_process_stdin_uses_saved_owner_and_never_restarts_missing_runtime`; `test_voice_native.py::test_native_capture_existing_action_once_with_authoritative_metadata` | Direct model fixture calls omit caller provenance and now hit the peer-mutation fence. No permission bypass or optimistic pass substituted. |
| `test_runtime_progress_backpressure.py::test_buffered_worker_events_yield_to_bridge_without_reordering_events` | Fixture worker row lacks `runtime_id`; the reader raises that KeyError, then lacks `ready` on cleanup. This lane did not establish it as an upstream baseline defect. |
| `test_session_health.py::test_recovery_is_independent_durable_and_never_executes_or_replays` | Direct model recovery fixture lacks caller provenance and is denied by the collaboration fence. Recovery/new-root authority requires explicit disposition rather than silently weakening creation checks. |

Skipped checks include unavailable tool-bash, filesystem, context-managed,
profiling, CLI and Python Playwright modules; unconfigured isolated CI; and
opt-in contribution, history-edit, recipes and warm-worker checks. Exact grouped
reasons/counts remain in the structured record. No skip is converted to a pass.
The parent owns the remaining full-suite qualification/disposition.

### Evidence and preserved live acceptance

Final logs/JUnit are under `/opt/collaborative-dtu/`: `gaps-relevant-91e22bfe.*`,
`gaps-build-91e22bfe.log`, `gaps-assets-rebuild-91e22bfe.log`,
`gaps-frontend-91e22bfe.log`, `gaps-browser-91e22bfe.log`,
`gaps-native-91e22bfe.log`, `gaps-wheel-build-91e22bfe.log`,
`gaps-wheel-install-91e22bfe.log`, `gaps-wheel-resources-91e22bfe.json`,
the three area logs/JUnit named above, `gaps-full-019bd6e9.*`,
`gaps-collected-019bd6e9.txt`, `gaps-split-manifest.json`,
`gaps-genuine-baseline-children.log` and `gaps-final-results.json`.
Earlier focused failures and successful replacements remain under their exact
source-suffixed `gaps-*` names; none was erased.

The independent model evidence on `47bc8c71` remains a PASS for discover/read,
one current-source grant, attributed peer input, independently produced/read
`7*11=77`, typed native sealing, exactly one requester continuation, an adjacent
commission and passive restart preservation. Its failed new durable-task startup
and unrun live in-flight steering are **still not qualified on the corrected SHA**.
Parent owns those actual new-task model checks and final Fable review. No unknown
admission was replayed and `/opt/collaborative-live-acceptance-47bc` was not changed.

No locked v1 documents, active host installation, environments or prior captures
were replaced. This continuation ran no host tests/builds/install/compilation,
and performed no push, PR, merge, deploy, teardown or external DONE-marker attempt.
Parent retains DTU/Gitea teardown and terminal-marker ownership. An older retained
pytest plan-fixture process (DTU PID 48965, source `47bc8c71`) was observed and left
untouched; no lane test/worker process from this continuation remains running.

## Corrective continuation: adapter loop checked; product acceptance remains open

This is the same lane and objective, preserving attempt 1 below. Independent
runtime inspection established that the missing final, steering and subscription
paths were app-adapter gaps, not demonstrated upstream blockers. The corrective
implementation does not relabel the earlier return or change accepted v1 files.

Implementation assumptions:

1. Retained current human input proves provenance, not semantic scope. The host
   therefore requests one explicit exact-scope approval for agent-mediated grants,
   keeps ordinary tool authorization, and rechecks source digest, workspace,
   root/generation and interruption revision afterward. A source already bound
   to a grant cannot be reused to widen authority under another command ID.
2. A sealed result is an agent claim plus native terminal evidence, never proof
   of artifact correctness. The persistence owner returns the exact canonical
   rows it saved, including system/developer filtering. Terminal IDs and current
   generation message aliases use those rows, not context indexes, prose equality,
   manufactured assistant messages or channel labels.
3. Steering uses the installed version-1 `live.steering` request-boundary submit
   capability under existing admission/ownership locks. Acceptance, applied,
   held and unknown remain distinct; held steering never starts a later turn.
4. One request-specific saved subscription yields one stable continuation ID.
   Its message, result seal and claim are published before dispatch. Existing
   queue admission checks grant/task/stop/owner/budget again. A startup recovery
   pass drains only known unsubmitted queues; submitting becomes unknown and is
   never replayed. Busy waits, and unrelated human pause/block is never cleared.

### Actual checks

Tested implementation source:
`060c2554a9debb8eb5b8466f336fd3769c96a6f5`.
The later evidence-only commit does not change that implementation.
All acceptance runs below used the existing parent-owned DTU; no second
environment, host app mutation, readiness-model replay or DTU teardown occurred.

| Area | Verdict | Evidence and limits |
|---|---|---|
| A: discovery, durable roots and result references | PASS, component/native scope | Ordinary task creation and configuration preserved; native source aliases, cold history and exact canonical terminal IDs checked. Native scripted-provider fixture resolves the result through `coordination.read`. |
| B: authority, attribution and delivery | PASS, component/native scope | Current human-source approval, forged/old/child/peer/generated/source-reuse rejection; actual native provider requests receive host attribution; anchored steering applies without cancellation. Stop/revision/revocation/owner and budget guards tested. |
| C: saved wait and continuation | PASS, component/native scope | Successful declaration/terminal linkage wakes once; busy, pause, stop, revision, revoked, active jobs, ack/defer/decline/failure, accepted-only, retry/unknown and restart recovery covered. Real runtime compaction plus an adjacent live-model round is not checked. |
| D: bounded awareness and installed resources | PASS | Existing receipt projections and on-demand skill retained. Built wheel installed into an isolated DTU target contains typed-reply skill and production assets. |
| E: useful cooperation as delivered to a real user | BLOCKED / unqualified | Deterministic production-service/Chromium complete loop passes, as does native Core/loop with scripted providers. Live-model efficacy, installed full worker-process/native-owner isolation, and live restart/compaction acceptance remain for parent qualification. Full repository regression is not green. |

Observed on the tested implementation:

- **326 Python checks passed, zero skipped**, including adapter, collaboration,
  coordination, service/runtime/stop admission, agent controls, canonical storage,
  standalone, history and browser-state suites.
- **440 frontend checks passed**, production build passed, and Chromium with
  `NODE_OPTIONS=--require=/opt/collaborative-dtu/chromium-env.cjs` passed.
  The browser records `qualifiedFinal`, `automaticDependencyContinuation`,
  `continuationOnce` and `completeLoop` as true. Its runtime and terminal anchors
  are deterministic emulation; it explicitly reports no native/live-model proof.
- `tests/fixtures/collaboration_native_probe.py` passed with Core 2.0.1,
  loop-live `bdd76badc58091ec47b55bb92cdf9e6f5bcc8e26` and context-simple
  `c7ec55db96888200ba619adedc857278daf787c5`. Two independent roots made 10 sender
  and 3 recipient scripted-provider requests, actual `app_control` calls, one
  simulated human approval, native in-flight steering, canonical multi-block
  terminal lookup and one continuation. The fixture independently read a concrete
  synthetic file. It uses direct sessions/serial event transport, not full worker
  subprocess ownership or a real model. No network model calls were made.
- Wheel build and isolated installation passed; packaged typed-reply skill and
  66 production asset files were verified. Assets/wheel are retained in the DTU,
  not committed into the source checkout.

Checks use the checkout's Python 3.13 environment with the already qualified
runtime site-packages on `PYTHONPATH` for installed native module coverage. The
checkout lock otherwise has Foundation `4e60f669` and no loop-live distribution;
the qualified runtime has Foundation `ab878820`. This deliberate check environment
selection is not an assertion that the normal installed-app resolver has qualified
the resulting composition.

The most recent full-repository fail-fast run, on `3c8df2c5`, stopped after
**523 passed, 5 skipped, 1 failed**:
`tests/test_canvas_draft.py:39` expected `Start a chat before opening Canvas` but
received `Choose a nonempty conversation identity` for a null UI target.
The same failure was reproduced on preserved source `4932263b`; it is not waived.
An earlier installed-module full run on `47bc8c71` reached about 76% with failures
before its 900-second bound; its identified pytest process was terminated. Neither
run is a full-suite pass. The inherited peer-pinning compatibility failure was
corrected with an exact presentation-only `session.pin` exception; execution and
settings fences remain.

Logs are retained under `/opt/collaborative-dtu/`: `corrective-tests-060c.log`,
`native-probe-060c.log`, `frontend-060c.log`, `build-060c.log`,
`browser-060c.log`, `wheel-build-060c.log`, `wheel-install-060c.log`,
`full-first-failure-3c8d.log` and `baseline-canvas-draft.log`. Earlier failed
checkpoint logs retain the multi-block rendering mismatch and incomplete native
test-fixture metadata/workspace corrections; passing replacements do not erase them.

One delegated Git checkpoint agent performed host Python syntax compilation
despite the DTU-only verification boundary. This is a recorded process violation,
not acceptance evidence; the implementation was subsequently checked in the DTU.
No host test suite or product build is claimed.

Parent owns independent real-model/installed-app qualification, final Fable
code/outcome review, full-suite disposition, any PR, and DTU/Gitea cleanup. This
lane has not pushed its branch, opened a PR, merged, deployed, edited accepted v1,
or retried the denied marker path.

## Attempt 1 status: preserved historical partial return

The following status and checks describe the earlier attempt only. Its upstream
limitation interpretation is superseded by the adapter investigation and corrective
evidence above; the original outcomes remain preserved.

The candidate adds human-issued task grants/revocation, attributed notify and
guarded idle queue admission, ordinary durable task roots with creator/brief/
configuration/output references, Related work controls and the packaged
`coordinate-work` skill. Grant issuance is an explicit human host action in
Related work, not automatic interpretation of a natural-language prompt.
Credentials are not copied into the task configuration; the normal host resolves
them. Output namespaces are guidance, not filesystem enforcement.

The selected loop-live runtime at `bdd76badc58091ec47b55bb92cdf9e6f5bcc8e26`
emits assistant text blocks without channel/result-kind classification.
`generation.finished` emits terminal text and `manager_turn_finished`, not an
attestation that the text is a final result rather than an acknowledgement,
progress, defer or decline. Installed-runtime inspection confirmed this.
`coordination.result` therefore reports `qualificationSupported:false`, and
`coordination.subscribe` explicitly returns unsupported with no execution effect.
No saved automatic dependency continuation is implemented. Peer steer likewise
returns unsupported; ordinary busy input is never relabelled as queued delivery.
This is **not the approved complete cooperation loop**.

| Acceptance area | Verdict | Observed evidence / remaining boundary |
|---|---|---|
| A: discovery, durable roots and Related work | BLOCKED | Root summaries, scoped reads, task/configuration/provenance and submitted-message links exercised; qualified exact result links depend on the missing final-result contract. |
| B: authority, attribution and delivery | BLOCKED | No-grant/current/revoked routes, actual child identity, committed admission fences, stop/task/budget checks and notify/idle queue tested; steer is unsupported and qualified final responses remain unavailable. |
| C: cursor and durable continuation | BLOCKED | Existing passive cursor/restart/reconnect behavior retained; no saved qualifying-response continuation can be enabled with current adapter evidence. |
| D: awareness, compact state and skill | PASS | Bounded affected-root references; no receipt scans on ordinary provider requests. Foundation namespace composition and installed-wheel skill resource checks passed. Explicitly disabled skills behavior remains respected. |
| E: useful cooperation acceptance | BLOCKED | Production-service/Chromium deterministic fixture consults, commissions, corrects, replies, independently reads a candidate and repeats after reconnect. It explicitly records `completeLoop:false`; no live provider efficacy or resource/isolation acceptance is claimed. |

DTU checks on source `4932263b94cc05d5949d231cbe517466876f80f4`:
202 focused Python checks passed across collaboration, coordination, service,
runtime, agent controls, builtin behavior composition and native/history queries.
The production frontend build and coordination browser fixture passed.
The frontend unit suite passed all 440 checks on that same source.
A built wheel installed into an isolated target contains
`bundle_data/skills/coordinate-work/SKILL.md` and production assets.
The browser uses explicit DTU Chromium flags `--no-zygote --disable-gpu`; default
Chromium launch on that environment is not claimed. Panel opening uses the
shared `view.update` navigation action; grant/message/task controls are rendered
UI interactions.

Peer queue requires an idle-start grant and a supported host admission adapter.
It never takes over a native owner. Unknown admissions and creations retain exact
IDs without replay; created roots record initial-input delivery separately.
User stop epochs, host update pause, task revision/pause and capacity denial are
checked again at admission. Passive `coordination.wait.results` are observations,
not successful dependency outcomes. Cold reads reuse the existing native reader,
which can parse one target's complete transcript for alignment; bounded returned
windows are not a claim of bounded underlying file parsing.

The parent manager owns independent/live checks, Fable review and the unresolved
runtime final-result/dependency-admission contract. No version bump, PR, merge,
release, deployment or teardown is part of this candidate. Generated frontend
assets and the built wheel are retained in the parent DTU, not refreshed in this
source branch; rebuild them before packaging/integration.

### Terminal recording boundary

The launcher-requested completion marker is outside this lane's allowed file-tool
write paths. The attempted write returned `Access denied ... is not within allowed
write paths`; no marker was written. No alternate tool or in-repository `DONE.json`
was used to evade that refusal. The parent manager owns terminal-status recording.
Final code checks and wheel/browser artifacts remain preserved in the parent DTU;
the supplied evidence directory also holds copied logs. This record is evidence of
the refusal, not an assertion that the launcher marker exists.

The **Tasks and workers** panel under Session details uses the same actions as
the agent app bridge. It watches explicit targets, sends follow-ups, and requests
interruption without selecting a different conversation or changing its composer
draft. There is no new scheduler, worker registry, or implicit delegation of
top-level conversations.

## Shared actions

- `coordination.list {sessionId?, limit?}` returns up to 100 conversation and
  worker summaries. A known conversation can be listed directly. Native saved
  child conversations remain in the existing subagent history view.
- `coordination.wait {targets, waitMs?, maxBytes?}` accepts 1–8 distinct targets.
  A target is `{sessionId, workerId?, afterCursor?}`. Without a cursor it returns
  the current snapshot. With cursors it waits for the first new report, completed
  turn, interruption, failure, pending question, or permission request, up to
  60 seconds. Tool/retry progress does not repeatedly wake it. `waitMs: 0` reads
  immediately. Missing targets return per-target errors.
- `coordination.followup {sessionId, workerId?, text}` uses the ordinary
  `conversation.send` path with draft preservation or `worker.message`, which
  submits an input to that exact live persistent child. A finite or retired
  child cannot silently be recreated. Use a stable action command ID for retries.
- `coordination.interrupt {sessionId, workerId?}` uses the existing conversation
  or worker stop path. It reports a request, never rollback or proof that all
  nested effects stopped.

Read actions neither start runtimes nor select conversations. They do not hold
the host command lock while waiting. Browser waits have their own independent
request path, so sending, navigation and stop controls remain available.

The authenticated app bridge supplies the calling root, actual native identity
and current generation. Own-root operations remain compatible. In the grant-free
candidate, same-workspace root messaging uses `coordination.send`, including the
`conversation.send`/`coordination.followup` aliases, without `grantId`. Peer worker
control, stop and settings mutations remain denied to models. A child cannot
borrow its root's authority. There is no model-supplied authorization flag.

## Delivery and recovery

Conversation results refer to their existing saved message IDs. Child results
carry a stable child session ID, real parent session ID, a fresh run ID, and a
new report ID for each report. Completing an idle persistent child does not
publish its last report a second time. An idle child is available for follow-up;
it is not evidence that its entire responsibility or saved task is complete.
Saved task IDs and worker operation IDs are separate references, not aliases for
conversation or run identity.

The existing worker record retains 32 report receipts with at most 20,000
characters each. These live beside its lifecycle state in the existing session
view file. They are omitted from ordinary browser progress snapshots. Existing
operation adapters continue projecting the authoritative worker row; no worker
lifecycle is duplicated in another operation journal.

Each wait target returns `nextCursor`, `results`, `cursorGap`, and `hasMore`.
Consume/deduplicate results by ID and persist the cursor only after consumption.
The advanced cursor suppresses already delivered results across reconnects and
host restarts. Retrying an unacknowledged cursor is deliberately repeatable;
transport delivery by itself cannot guarantee exactly-once consumption. Retention
expiry or a rewritten transcript returns an explicit gap. It never replays work.

Text output is bounded to a caller-selected 4–64 KiB across all targets, with at
most 16 receipts per target/page. A truncated result is labelled; `availableBytes`
describes the retained excerpt, not invented original output size. Child source
truncation is preserved separately. The complete available conversation remains
in its existing saved history. The browser keeps up to eight recent excerpts per
watched target and bounded delivery state in tab-local session storage, independent
of the client ID that rotates on reload. It retries only failed reads after a
connection loss, never a follow-up or interruption.

Worker follow-up command receipts share the existing command store. A submission
that loses its acknowledgement remains `unknown`; retrying its command ID returns
that receipt without dispatching again. Unresolved receipts become unknown on
host restart, and persistent idle workers become interrupted because their old
runtime no longer owns them. Reopening the app does not restart them.

Every admitted `conversation.stop` increments the existing session's
`interruptionRevision` and records `lastInterruption {commandId, at, origin}`.
Worker stop does the same in its worker row. Duplicate command IDs do not advance
the revision twice. Scheduled adapters can bind and recheck this revision before
submission; internal runtime parking/reloading does not represent user stop intent.

## Portable boundary

`amplifier_operations.coordination` is a standard-library-only protocol in the
existing operations package. `delivery(snapshot, after_cursor, max_bytes,
max_results)` provides storage-neutral result cursors. `ChangeSignal.wait(read,
wait_ms)` provides bounded event notification and captures its event before
reading, avoiding a read/wait lost wakeup. The host owns storage, authority,
identities and lifecycle. The library does not import the app or run tools.

No loop-live dispatch setting changes. Programmatic dispatch remains opt-in.
No Work composition, dependency source pin, or release version changes.

## Validation

`pytest tests/test_coordination.py` covers multi-target waits, attribution,
attention, output bounds, gaps, cancellation, reconnect, restart, uncertain sends,
command idempotency, and agent scope. `tests/fixtures/standalone_children_probe.py`
also exercises real Foundation/Rust child sessions with a deterministic
orchestrator, including explicit message receipt IDs and report lifecycle.

`npm run test:coordination-browser --prefix frontend` runs an isolated production
service and production assets in Chromium. Its deterministic runtime supplies
worker events; it verifies two watched targets, a follow-up, reconnect,
interruption during a wait, draft/selection preservation, and mobile bounds.
This is actual service/browser coverage, not a claim of a live provider run or
deployed Work bundle acceptance.
