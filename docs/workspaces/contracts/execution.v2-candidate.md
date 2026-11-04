# Workspace execution v2 — candidate

Status: exact clause proposal encoding the [approved direction](collaborative-workspaces-decision.md), not publication of a replacement contract. Target: `execution.v1.md` as read at source `2e310274d3e401200d930c9e7a7e900563eaeaa1`. The accepted target remains unchanged. Newly authored normative wording needs the applicable decision/publication checks.

## Evidence and reason

The user reported effective cross-chat coordination in their OpenAI workflow and asked Unified to adopt a productive collaboration model, not make checkout locking its prerequisite. Read-only inspection of that installation found independent root task conversations, substantial recorded peer messaging/waiting, and command-specific worktree locations while the chat's starting directory stayed a shared project root. These observations establish actual use and placement flexibility, **not** conflict-free shared editing or measured cost of serialization.

Unified's existing `IMPLEMENTATION.md` explicitly lists cross-session conflicting-writer coordination as unfinished. Removing the universal claim therefore need not remove an implemented lease here, but other consumers' reliance has not been exhaustively checked. The cost/trade-off is explicit: cooperative sharing permits concurrent work and avoids mandatory copies, but cannot guarantee prevention of competing edits. Stable verification still needs a preserved or quiesced candidate.

## Exact replacements

### Definition paragraph

Replace the paragraph beginning “A change set groups” with:

> A change set groups one piece of ongoing work across repository checkouts or document drafts. Its execution bindings carry a revision, explicit locations, and the declared sharing mode. A conversation has one native execution owner; that ownership is not a checkout-wide write lock. An isolated working directory is not a security sandbox.

Replace the two illustrative JSON records with:

```json
{"changeSetId":"change1","sessionId":"native-session-1","mode":"workspace-folder",
 "bindings":[{"repositoryId":"repo1","baseCommit":"abc123","checkoutId":"co1","sharing":"cooperative"}],
 "executionRevision":4}
{"helperId":"helper1","parentId":"native-session-1","access":"read-snapshot",
 "snapshotId":"snapshot1","scratchId":"scratch1"}
```

### Promise 3

Replace promise 3 with:

> **Sharing is explicit.** A checkout may be shared cooperatively by independent conversations. Its reported sharing mode must not imply enforced single-writer protection. Supported version-aware write APIs refuse stale-source updates; arbitrary shell programs and external editors remain outside that guarantee. A protected execution backend, when supplied, must report its actual enforcement and refuse stale owner generations before claiming protection.

### Promise 5

Replace promise 5 with:

> **Concurrency choices belong to tasks.** Ordinary work remains in the chosen shared folder. Independent tasks may coordinate repository/file responsibilities or use separate per-repository working copies when overlap, experimentation or stable verification calls for isolation. Each chosen path and baseline is visible. Creating or attaching a conversation does not silently relocate it, include unrelated dirty work, or require an execution-mode decision before discussion.

### Promise 6

Replace promise 6 with:

> **Bindings describe actual execution.** Preserve the canonical history/configuration home separately from default execution directory, per-repository checkout bindings and actual operation cwd. Shell commands, file tools, dependencies, tests and supported preview launchers must identify the binding actually used. A claim about a preserved snapshot must not silently import a peer's mutable dependency or test changing source. Ordinary explicit paths remain subject to the executor's actual permissions.

### Not in v2

Retain the hard-isolation limitation. Replace the automatic-background-relocation item with:

> **Automatic background relocation** requires explicit task-level authority and visible placement. A declared sharing mode or cooperative file plan neither grants new filesystem permissions nor substitutes for protected execution.

### Checks

Replace P3 with:

> P3: inspect two cooperative writers and verify their mode does not claim protection; race a supported version-aware file write and require an explicit stale-source result. For any backend advertising protected ownership, restart/reassign and reject writes with the old generation. An unsupported protection check remains unsupported, not a pass.

Replace P5 with:

> P5: run coordinated tasks in the chosen shared folder and an explicitly isolated multi-repository fixture. Verify actual paths, retained original work, declared sharing modes and versioned test inputs. No communication operation alone relocates source or claims isolation.

Other checks remain unchanged; apply the revised promise 6 to P6's source/runtime provenance check.

## What does NOT change

Native history and one execution owner per conversation; passive inspection; folder attachment preservation; bounded child assignments; scope and tool permissions; private drafts; original-document protection; exact-version and dependency evidence where claimed; deliberate integration, publication and cleanup; user stop/revocation; AHP/ACP interoperability. Peer collaboration does not convert independent chats into children or grant unrestricted authority.

## Migration and acceptance

### Owned environment recovery and retained files — 2026-10-15

Each new isolated test environment retains its exact qualified backend
configuration and the observed physical backend identity before launch. Changing
the active backend does not strand cleanup if that original installation and
connection remain available and verifiable. Fresh execution still uses the
currently approved configuration. Missing or changed original code and identity
are explicit recovery gaps; callers cannot supply replacement executables or
connections through an environment request.

User and agent callers share an explicit revision-checked reconciliation action.
It inspects current state, records its own durable receipt and can repair the
resource projection. It never launches, reruns, stops or deletes physical work.
An absent instance releases logical ownership only after exact backend, owner
and launch identity checks and proof that no unresolved admission can still
create it. Failed inspection and legacy receipts without sufficient evidence
retain ownership. Original uncertain command outcomes remain unchanged.

Routine removal retains the source snapshot, working folder and artifacts before
deleting the owned environment. The Incus adapter defaults to selected-file
retention; explicit full-environment retention remains available. Export failures,
unsafe selected content and incomplete preservation leave the stopped instance
available for recovery. Retained byte accounting comes from durable verified
manifests, not repeated directory scans. It distinguishes saved-file bytes from
temporary export space and daemon image/cache usage. Older missing accounting
is unknown. Unique files and unresolved evidence have no automatic age expiry.

Qualification covers changed active configuration, lost acknowledgements,
projection failure, foreign ownership, stale revisions and unknown admission;
the archive path covers missing roots, unsafe paths/links, truncated exports and
bounded storage. Controlled fixtures, actual backend tests, provider cooperation,
browser behavior and installed-release adoption remain separate evidence.

### Handoff at a saved runtime boundary — 2026-10-15

A managed checkout handoff returns its durable pending receipt to the calling
tool before changing execution placement. The host waits for the current root
generation, tool replies, approvals, pending inputs, operations and native
children to settle and for the worker to checkpoint and park. A saved goal yields
at its completed-turn boundary with its progress and budget intact. An idle UI
event alone is not a release proof; waiting must not cancel an in-flight tool or
borrow another writer's lock. Once settled, the existing native ownership protocol
confirms release and retires that worker before the placement revision changes.

The receipt stores positive release evidence before committing placement. After
a host restart, a retained positive boundary can finish its original placement
without replaying input. An interrupted preflight or boundary wait rejects the
unperformed move with its source unchanged. An unconfirmed release remains
unknown and requires deliberate reconciliation. A later committed placement
suppresses an earlier move's pending notification; recovery never moves it back.
Completion requires the matching placement in the saved owner, including the
handoff identity for new moves. A receipt saved ahead of that placement remains
fenced while recovery retries it; failed recovery retains an unknown receipt
that can be reconciled explicitly. A notification failure after confirmed
placement does not make the completed move unknown.

If active work requested the move, the host uses the existing guarded inbox to
deliver one completion observation and continue the already authorized task in
the destination. The original input and tool call are not resubmitted. The
notification preserves the originating user-message, interruption, task and
placement anchors; Stop, new user input or changed task authority suppresses it.
An exhausted or ended goal is not re-armed by the move's completion observation.
The host captures the calling native input lineage at request time and retains
its origin and causal depth. Configuration grants retain that original ancestry:
a lower-authority peer cannot borrow the moved chat's grants through its return.
An outcome does not grant new authority. Failure to deliver that notification
cannot turn a positively committed placement into an unknown move. Idle explicit
moves do not start work. Canonical history/configuration homes, drafts, voice,
attachment identities and file-access restrictions remain unchanged.

Qualification covers actual native worker processes, saved goal budgets and
persisted history with a scripted provider, plus interrupted boundaries, foreign writers, changed
authority, duplicate commands and restart gaps. Real-model cooperation with the
instruction to finish its current turn, browser acceptance and installed release
adoption remain separate checks.

### Supported file-tool source checks — 2026-10-14

Unified wraps the mounted reference filesystem tools and both reference patch
engines at the existing root/child app-access boundary. Successful file reads
record an in-memory content version for that native coordinator. Updating or
deleting an existing file requires that version; a stale or missing observation
requires a fresh read. Successful writes advance only the invoking coordinator's
observation. Restart, remount and native children begin without inherited file
observations. Directory listings and failed reads do not authorize replacement.
Creation requires absence; patch moves require an absent destination.
Function patches touch each resolved path once; combine changes within one
file operation instead of relying on sequential duplicate path operations.

Short per-file locks in one app data home serialize the comparison and supported
operation across worker processes. Patch calls preflight every source and move
destination before invoking their engine. This prevents stale-source rejection
after earlier patch operations have already run; it does not make a multi-file
patch an atomic transaction against engine, OS or process failure. On an uncertain
or failed mutation, invalidate affected observations and require fresh reads.
Cancellation releases locks and does not replay a mutation.

The lock/observation domain is bounded to these wrapped tools in one installation.
Existing path policies remain authoritative. Unknown tools, image generation,
shell commands, external editors, separate app homes, network-filesystem lock
semantics and externally changed path aliases are outside this guarantee.
Content versions detect changed bytes, not edit history or change-then-restore.
Reads may be paginated; observing a version does not assert that every line was
shown. Observations are bounded and evicted entries require another read.
No new transcript store, mandatory workspace lease or automatic merge is added.

Qualification must mount the real reference tools, exercise both patch engines,
race separate worker processes, cover child/remount separation and preserve the
existing read/write path restrictions. Source-only doubles do not establish
native-runtime coverage or installed-worker adoption.

### Active-work awareness and declared file intent — 2026-10-13

The host supplies a bounded, fresh projection of its observed active work in the
calling conversation's canonical workspace at each supported provider request.
User-created roots, commissioned roots and observed native children remain
distinct. Include actual recorded execution folders and declared file intent,
never peer drafts, prompts, transcripts or credentials. This projection uses
existing catalog/lifecycle facts without loading dormant histories or runtimes.
Historical metadata may require the existing catalog index to be rebuilt; a
steady-state read visits active work rather than every saved conversation.

A root may replace its own advisory file intent using the observed intent and
execution revisions. The user may edit a selected root through the same action.
Native children cannot replace their enclosing root's declaration. Declarations
are explicit shared data, normalized to known task folders when submitted; they
grant no filesystem access, block no operation, and imply no reservation or
stale-write protection. Empty paths clear the declaration. Stable command retries
retain their original outcome, and stale revisions preserve the newer record.
Declaration bodies live in the existing command receipts; session metadata
retains only the current reference and bounded summary. Historical declarations
therefore do not put every saved path list into the resident chat catalog.

Active peers with intersecting declared paths receive an overlap indication.
Separate copies retain distinct locations. A changed execution placement marks
an earlier declaration for review rather than silently translating its paths.
Idle declarations remain retained but do not claim current activity. External
writers, other app installations, unobserved workers and later filesystem alias
changes remain outside this observation. Partial/truncated coverage is explicit.
Client drafts remain private through refresh, conflict, navigation and reload.
Live awareness is ephemeral reference data and must not accumulate in native
conversation history or grant peer control. Dynamic observations are added at
the existing fitted provider boundary, not retained as native reminders. Each
request receives one current observation even when the loop retains reminder
text within a turn. Bridge failure means unknown coverage, never no peers.

### Explicit repository selection — 2026-10-13

A task may inspect, create or attach a working copy for an explicitly selected
repository inside its canonical workspace, current execution folder or an
already associated ready checkout. The surrounding workspace need not be a Git
repository. Relative repository paths resolve against the reported execution
folder; successful inspections return the absolute repository root to carry
into later actions. A different repository's checkout cannot be attached under
the selected source identity. Existing exact source revision and dirty-copy
rules continue to apply independently to each repository.

Creation and attachment command identities are serialized across repositories
sharing managed storage. Concurrent reuse with different contents must preserve
the first retained record and reject the competing request.

Selection and creation do not move the conversation or remap shell imports,
dependencies or file tools. Checkouts retain their repository identity, source
and location in the existing worktree owner. Choosing a default execution
folder remains an explicit handoff. Multi-repository execution mapping and
combined runtime provenance remain a separate, unqualified step. Root worktree
mutations require root authority; a child cannot relocate its enclosing task.

### Cooperative resource reservations — 2026-10-13

Explicit reservations extend the existing resource lifecycle owner. Inventory
observations remain observations; they never acquire or release a reservation.
An exact resource key has either one exclusive holder or explicitly shared
holders. Admission is atomic across workspace registrations in the same app
data home. Separate app installations and resources reached under different
keys are outside that guarantee. A reservation neither provisions infrastructure
nor fences arbitrary shell programs, external services or filesystem writers.

The host binds an agent reservation to its actual root/child principal. An agent
may release or mark uncertain only its own reservation; a user may explicitly
reconcile any retained reservation. Peer messaging grants no resource control.
Stop, disconnect, expiry and workspace removal never imply teardown or silently
release a held resource. Uncertain reservations continue to block new holders,
including shared holders, until explicitly reconciled. Release relinquishes a
claim with evidence and does not claim physical teardown.

Stable command IDs retain their exact results across restart, including release
and later reacquisition. Retries cannot resurrect an old reservation. Updates
require the observed revision. Admission uses global Advanced execution limits,
defaulting to 0 (no count ceiling) both per root (including its children) and
per app data home. Explicit positive limits gate new acquisitions; existing held or uncertain claims remain
readable and releasable at the limit. Reads are paginated, passive and do not
load or resume conversations. Shared actions serve both clients and agents.

### Coherent commissioned-task operation — 2026-10-13

A new commissioned conversation has one durable creation handle. The host
captures its configuration and execution placement, reserves the requester's
return notification by default, and commits the initial request and that wait
before admitting execution. An explicit `returnNotification:false` permits
independent work without an automatic return. Inspecting the creation handle
resolves its initial request; callers do not construct subordinate command IDs.

Preparation phases distinguish a committed plan, requested root creation,
retained root and committed input. Restart may resume a proven unperformed step
or reconcile an existing exact receipt. An unproven root-creation effect remains
unknown and is never blindly repeated. Stop, changed assignment, changed
placement or revoked admission suppress remaining automatic preparation. Reads
and duplicate requests remain passive. Private captured configuration is never
included in public receipts or ambient coordination context.

### Approved initial repair implementation — 2026-10-13

The initial program separates defect repairs from larger design experiments.
The shared-folder default and independent-chat model remain unchanged. A peer
created from a moved task inherits its actual execution directory without
claiming a new isolated copy. Typed failure/deferral/decline and authenticated
runtime-failure observations may wake a saved waiter; waking is distinct from
successful dependency acceptance. Inbox capacity may reject new input with
explicit backpressure before any message is added, while reserved result slots
preserve accepted waits. Result navigation must verify the exact referenced
bytes and retain the producer's execution binding; absence or mismatch is an
explicit limitation. These additions are tracked in the implementation evidence,
not a claim that every v2 proposal or original v1 isolation promise is delivered.

No current universal checkout fence is known in this source baseline; this is not proof that all integrations are independent of the earlier promise. Existing explicit worktree handoffs, permission checks and preservation checks stay in place. Consumers must distinguish cooperative from genuinely protected bindings and report unsupported enforcement. The first collaboration increment should retain existing folder behavior, add honest context/coordination, and not claim newly enforced write isolation.

### Retained exact results — 2026-10-14

Optional reply `messageIds` name the recipient's assistant output from the
current generation, never the incoming request's message ID. The host checks
retained messages and checkpoint anchors before artifact preparation and again
before committing a declaration. Invalid or unavailable IDs return actionable
repair without consuming a response revision; omit them when no current output
ID is known, keeping the exact `requestId` and evidence references. This early
check does not replace exact native-message resolution and terminal sealing.
Previously recorded rejected revisions remain retained.

A fresh response declaration snapshots supported local `path@sha256:<digest>`
references before the reply is acknowledged. The existing output/resource owner
retains matching bytes, their producing execution binding and the request
relationship. Files remain inside the producer's execution folder. Larger files
use a flat manifest of bounded pieces in the existing resource store; there is no
fixed byte ceiling per file or declaration. Current free disk space governs
preparation, and text previews remain bounded. Complete saved bytes are verified
before a download starts. Reads happen outside the host lock;
generation, Stop, placement and response revision are rechecked before commit.
Snapshot and reply records commit together. Exact command retries return the
retained result without rereading mutable files, including after restart.

Missing, changed, unsupported or resource-constrained sources remain explicit artifact
states in the recorded report. An incomplete exact reference cannot satisfy a
successful dependency. Retained bytes prove the referenced version, not semantic
correctness, successful integration or independent review. Opaque references
remain unverified. Older replies keep their checked-source fallback and are not
retroactively labeled retained. Request-scoped artifact reads expose only the
declared artifact to the request participants in the recorded workspace; they do
not grant general access to the producing conversation's output library.

`outputs.image` and `outputs.images` use that same request-reader authority for
an exact retained PNG from another conversation. The output's saved reply
command selects the original response revision; later replies cannot substitute
new bytes. The caller still supplies the exact output ID and SHA-256. Image reads
and validation happen outside the app lock, then every selected relationship is
rechecked before any pixels are returned. Ordinary output-library reads remain
conversation-owned; this path does not copy files or grant new permissions.

Image observations describe only the prepared model request, not an acknowledgement
from the provider or a revision to earlier visual evidence. The existing inspection
receipt/tool-call identity remains the correlation key; request preparation and
pre-transport omissions name timeout, changed input/selection, missing receipt,
unavailable vision or unreadable saved bytes separately. A failed later read does
not establish that the saved image hash changed or that an earlier request lacked
pixels. No new observation store or retry loop is introduced.

The discovered reply schema and shipped coordination skill describe the exact
syntax, an executable example and the acknowledgement's retention status. A
local reference with an explicit but malformed `@sha256` or `#sha256` marker,
or an unsupported `file:` prefix, records an `unsupported` artifact with repair
guidance, without reading a file. Such a declaration is incomplete and cannot
qualify as a successful dependency. Remote URLs and ordinary opaque citations
remain references, without filesystem authority. A plain path alone does not
request retention. Replies distinguish no saved files from incomplete retention
and saved exact bytes; terminal sealing cannot change a retention failure into
saved bytes. Correction uses a later response revision, preserving the original.

### Observed dependency provenance and combined candidates — 2026-10-14

An explicit passive worker inspection reports already-loaded module origins,
actual mounted object types, and bounded current-file hashes. It never imports
requested project code, installs dependencies, starts a dormant worker or
claims checkout isolation from an editable dependency's origin. Loaded origin,
file bytes observed at mount, and file bytes observed now are distinct facts;
none retroactively proves exact imported bytecode or a successful test.
Worker readiness captures only bounded in-memory identities and never waits for
source-file hashing. File bytes are inspected only on an explicit provenance
request. Without a real earlier file observation, mount-time hashes and
change-since-mount comparisons remain unavailable; a later read cannot supply
them retroactively.

An explicit combined candidate can name up to eight inspected repository
snapshots and sixteen expected loaded project-module origins/file hashes.
Existing command receipts retain capture/check results. The host validates
repository scope against this task's workspace, execution folder and associated
working copies, rechecks placement and managed associations after reads, and records mismatch or unknown
rather than substituting another dependency. Later checks compare these exact
identities and flag source/dependency drift. This is bounded evidence for the
listed repositories/modules, not a hermetic environment, dependency installer,
test runner, or blanket proof of integration or release readiness.
Passive list/read and the advanced task-execution disclosure expose the same
retained observations as agents and session commands. File/worker reports and
the total inspection duration are bounded; deadline/cancellation cannot create
a late candidate receipt. A replacement worker makes prior loaded-state
continuity unknown even when current source still matches.

### Explicit integration validation — 2026-10-14

Combining an assignment's outputs requires an explicitly named integration
responsible member. The commissioning owner may assign or clear that duty with
the current assignment revision; it never enrolls an independent conversation.
Independent conversations can validate their own candidates without creating an
assignment. Responsibility records coordination, not wider file/tool authority.

An explicit validation names a retained candidate, one of its repositories and
the exact command and timeout. It checks the complete candidate before
submitting through the existing Operations shell policy/approval owner, then
rechecks admission after approval. The current worker must positively confirm
support; replacement or older adapters refuse before submission. Stop, responsibility and execution changes
invalidate delayed admission. This is an observation boundary, not a filesystem
lock against external writers. Stable command identities retain uncertain
admission without repeating commands after lost acknowledgment or restart.
If no parent validation or collection receipt committed, retrying takes fresh
internal source observations. Completed parent retries return their retained
receipt without repeating observations or command execution.

Explicit collection reads that operation's native terminal evidence and checks
the candidate again. Command exit, output completeness, source/dependency match
and retained artifacts remain separate facts. A zero exit and matching candidate
permit a successful validation receipt only for that named command/repository;
they do not prove the command was a meaningful test, that every repository was
integrated, or that anything was published. Missing terminal evidence remains
unknown. Resulting repository commits/snapshots and supported exact file
references use existing command and output owners. Collection preserves earlier
receipts, including partial failures and changed candidates.

Per-repository publication status defaults to unknown. Explicit reports carry
their author and references, distinguished from any existing authoritative
publication evidence. Recording a status never merges, publishes or deploys.
Passive read/list and progressively disclosed user controls share the agent
actions and do not execute validation or collect mutable source automatically.

Before publication: verify target revision and exact payload, decision coverage, affected consumers/checks and dated changelog through the supported publication guard; preserve this proposal. A refusal stops publication, not ordinary already-authorized implementation. DTU and Fable findings will be recorded against the exact implementation revision; none are claimed by this candidate.

### Configuration authority addition — 2026-10-14

Configuration authority is a host-issued, revocable grant to one root conversation
for named configuration effects and explicit scopes. Sharing a folder, receiving
a peer request, copying a mount plan, or spawning a scheduled/recipe child does
not convey the source conversation's administrative grants. Existing provider
and model selection remains an ordinary own-conversation action. Persistent
connection, module/source, file-access and bundle changes, and replacement or
enabling of the runtime mount plan, require an applicable grant for agent actors.
An omitted persistent scope never silently selects global scope. App-wide default
actions have an explicit global effect; alternate default-setting routes receive
the same check. A provider-connection grant includes its executable source and
credential configuration, as displayed in the approval.

The existing command receipts own grants, requests and decisions. A user may
grant explicitly through the shared action API, or allow an agent's attributable
request through the ordinary approval control. The authorization remains useful
across subsequent turns until its expiry, revocation, changed workspace or Stop;
it is not re-requested for every matching settings action. The host requires the
actual current root generation for an agent to request or use it. Native children
retain their own identity and cannot borrow root administration. A fresh peer
request intersects the receiver grant with its authenticated senders through
bounded retained input lineage; revoking a sender also fences delayed effects.
A human can expressly approve the current peer request. Host-created returns to
the receiver’s own saved dependency wait preserve that receiver’s original grant
only through verified retained original input lineage. One-shot observations and
creation-failure returns retain that lineage too; missing legacy observation
lineage requires explicit approval of the current request, not an assumption that
its origin was human. Scope names
describe configuration ownership, not filesystem confinement or read authority.

Accepted delayed changes retain the selected grant revision and interruption
revision. The host checks these after waiting and before supported settings,
credential, validation and runtime-control effects. Queued changes survive
restart with that provenance; revocation or Stop suppresses unperformed effects
without retrying them or discarding the requested configuration. A request already
admitted to an external authentication process, validator or worker can complete;
revocation does not undo external effects. Saving configuration is distinct from
mounting it. User actions use the same domain checks and do not require an agent
grant. No grant permits a peer mutation, grants user approval to an agent, or
adds OS protection around arbitrary shell programs or external modules.

Native root `app_control` requests park the current tool call in the existing
worker approval map before the host publishes the approval. The durable user
decision resolves that waiter; the agent then chooses its next action, which
still checks current authority. No privileged effect is stored for replay.
Raw API requests return a pending receipt without parking an agent. A decision
after worker loss saves authority but never starts or replays the lost turn.

## Registered own-history scope addition — 2026-10-14

The authenticated same-user app/tool capability already permits explicit access
to registered own-history across workspaces. Read scope selects that existing
library; it is not a new grant database, assignment privilege or multi-tenant
boundary. Agent history discovery, waits, transcript windows and exact-message
reads default to the caller's workspace and accept explicit `scope: all` for the
registered library. Recall source reads retain the search scope explicitly and
check it again with the original source revision. Applicable shared session
history actions use the same scope rule. Human UI selection of an exact registered
conversation retains its existing library access without another approval.

`scope: all` does not widen request-result participation, start/stop/steer
authority, source references or output-library ownership. Request results retain
the request participants and saved workspace checks. Artifact references do not
authorize listing or reading another root's private output library. Existing
per-session HTTP endpoints remain exact-target user-client routes. All these
reads remain passive and never acquire a native writer or replay work.

## Assignment control addition — 2026-10-14

### Optional shared task list

An explicit assignment may keep at most 128 work items in its existing canonical
assignment receipt. The commissioning owner creates and edits unclaimed items,
sets dependencies, cancels unfinished items, and explicitly reassigns responsibility
to a current bound member. Bound members can read the list and atomically claim a
ready item for themselves. A claim records responsibility, not a running process,
file lock, permission expansion, or instruction to start work. Independent chats
are never enrolled by a task action. The bundled UI and authenticated agent use
the same `assignment.tasks.*` actions.

Each mutation has a stable command ID and retained receipt. Creation checks the
assignment revision; item transitions check the item revision. Claim-holder
release and completion also require the exact opaque claim ID. Concurrent claims
have one winner. Exact retries return their original receipt without applying a
transition again; a receipt is historical evidence, not renewed authority.

Dependencies must be existing items in this assignment and form an acyclic graph.
An item is ready only when its dependencies are reported complete and it has no
explicit block. Completion requires the current holder's valid claim and records
their report; it does not verify artifacts or automatically execute dependents.
Completed and cancelled items cannot be edited or reopened. Their retained
identity cannot silently change meaning underneath a completed dependency.

Claims survive ordinary host restart and later turns of their still-bound holder.
There is no expiry, automatic release, reassignment, execution, or replay. Explicit
Stop, changed assignment membership, or an unavailable owner makes the retained
claim stale or unavailable without freeing the item. A stopped assignment cannot
admit new claims or completion. The holder or assignment owner can explicitly
release a retained claim; only the owner can cancel or reassign. Reassignment
issues a fresh claim ID and revision, fencing the former holder. Release or
reassignment does not assert that prior external effects stopped or are safe to
repeat. User-visible status distinguishes readiness, responsibility, stale
authority, and reported completion.

Task list reads also expose the current holder's observed activity separately
from those ownership facts. A holder can be working, awaiting approval or an
answer, blocked, paused, stopped, failed or unavailable while retaining its
claim. Its saved blocked reason is bounded reference data. Passive task-list
waits include these observations in their change token, without incrementing
the assignment/item revision, loading transcripts or starting dormant runtimes.
The same projection serves user and agent reads. A status observation never
releases a claim, completes a dependency or authorizes takeover.

New commissioned roots share an explicit host-owned assignment with their
commissioning root. Existing peers and historical commissioning records are
never enrolled retroactively. Native descendants share their root's admission.
Membership, limits and Stop are retained in existing command receipts; model
usage stays in existing execution receipts and is attributed at admission.

Assignment limits cover aggregate supported model calls and open commissioned
roots. New assignments have no execution count ceiling: `maxOpenTasks`,
`maxMembers` and `maxConcurrentCalls` default to `null`. Each accepts a positive integer for an
explicit ceiling or `null` for no count limit (blank in the UI). Existing saved
finite values are preserved until their owner explicitly changes them. The
retained member roster has no arbitrary maximum; `maxMembers` counts current
bound and reserved members when explicitly set. Removing execution ceilings never discards members or unknown
receipts, changes Stop/ownership, or bypasses token/cost budgets, the installation's
separate per-source task-chat policy, or host resource admission. Optional
token/cost reservations are declared allowances per call, not
provider-enforced maxima. Settled receipts replace their allowances; missing
outcomes remain unknown. Limits cannot promise an exact spend cap. Already
running effects may finish. Admission is serialized before provider effects.

The host-wide model waiting policy defaults to no queue-count or wait-time
ceiling (`maxWaitingCalls=0`, `waitTimeoutSeconds=0`). Positive values are optional
operator limits. Waiting uses the existing admission queue and resumes on capacity
changes; removing the count ceiling does not admit calls beyond a selected
assignment allowance or bypass provider admission. Existing saved finite settings
remain in force until changed, including after restart. The global settings UI
offers a single action to select unlimited waiting without resetting spend,
already admitted calls, or Stop.

`modelCapacity.set` also accepts standing Full from an explicit calling
conversation: its current native root or assigned descendant, or a trusted host
system. It needs no browser or new human message. The global policy owner retains
the actual actor and source, binds each command to that source and exact settings,
and checks captured Full, Stop, execution and placement identity at commit. A
normal turn finish does not cancel an accepted save. An exact retained command is
observable by the same source after completion or revocation without renewing
authority or repeating the change. User controls and Ask behavior are unchanged;
`modelCapacity.read` remains passive. Saving wakes existing capacity waits and
never starts/restarts workers or changes spending limits. Once committed, the
policy remains durable configuration independent of its setter's lifetime.

The commissioning owner may manage the assignment; members may inspect it.
Membership does not confer control over existing independent peers. Assignment
Stop closes admission durably before interrupting still-bound roots and their
native children. A stopped assignment is not automatically resumed by a late
reply, restart, schedule or another input. The user may explicitly detach a
conversation after interruption settles; retained usage and late corrections
remain attributable. Detaching never interrupts another conversation or erases
accounting. No cleanup or external resource teardown is implied by Stop.

Orderly host shutdown closes admission before asynchronous cleanup and marks its
owned live workers as expected to exit. This maintenance boundary does not act
as a conversation or assignment Stop: settled conversations retain their state,
unfinished execution is interrupted, and no work is replayed. Unexpected worker
exits and worker-reported failures remain errors. Generated systemd units signal
the host first, retaining bounded final process-group cleanup; existing custom
units require an explicit reviewed replacement to adopt that policy.

The current adapter cannot interrupt or account for only one request inside an
independent peer's mixed conversation. Stop reports that limitation and suppresses
pending assignment input without substituting a whole-conversation interruption.
When a token or cost limit is enabled, new queue/steer requests to independent
peers are refused with this limitation; commissioned helpers share the budget.
Informational notices remain available and do not start independent work.

## Preserving checkout archive addition — 2026-10-14

An inactive app-created checkout may be archived by moving the entire registered
Git worktree into its owner's private retained storage. Archiving is distinct
from deletion: dirty/index state, ignored and unknown files, symlinks, nested
content and detached commits remain in that checkout. No ignored pattern is
interpreted as permission to discard data. Git may refuse unsupported layouts;
the original is preserved. This does not claim an immutable snapshot against
external writers or reclaim disk space.

Archive and restore use stable operation identities, expected ownership revisions,
and retained before/after directory identity. After interruption, inspect actual
Git registration and directory identity to confirm a move or report uncertainty;
never repeat an uncertain move. Only app-created, unused checkouts qualify.
Active execution, pending/unknown handoff, attached folders and destination
collisions are refused. Restore does not start work, replay input or change history.

## Reply revisions and bounded observations — 2026-10-14

A delivered request remains addressable by its original recipient in a later
current root generation, within its unchanged workspace, task and interruption
boundary. Later declarations require the observed response revision. Every
accepted declaration has a monotonic revision and a stable operation identity;
exact retries return the same receipt and conflicting payloads are rejected.
Earlier declarations remain in existing command receipts. A question or deferral
is a report, not request completion. Meaningful declarations and automatic outcome
notifications have no lifetime count ceiling: continued work retains the original
request and operation identities. Each payload and result view remains bounded;
`coordination.result` returns the latest 32 declarations without discarding older
command receipts. This does not relax queue pressure, Stop or task budgets.

Existing current-protocol version-two waits stopped solely by the former automatic
notification ceiling may recover through ordinary admission. Recovery requires the
exact legacy diagnostic and a saved outcome revision greater than 32; it preserves
the continuation identity and last claimed revision. Generic limits, cancelled or
expired waits are not reopened, and existing unknown notification receipts are not
replayed. Restart recovery does not itself grant execution authority.

Typed negative reports and questions can notify the requester immediately as
unqualified agent claims. Exact successful root terminal evidence can qualify a
result separately; unrelated background jobs do not erase that evidence. Only a
qualified successful result satisfies the dependency. The current recipient
generation may correct its staged report using the expected response revision. Earlier declarations
and retained artifacts remain inspectable as superseded, never qualified by the
later terminal event. A different generation cannot take over an unsettled report.
Each report revision produces at most one automatic continuation, while rejection
or unknown terminal qualification remains inspectable. Every continuation retains the requester's
saved task and Stop boundary. A newer meaningful report supersedes an
unsubmitted earlier success notification, preserving both receipts and preventing
stale dependency success. Requests made under older single-notification
subscriptions keep that behavior until explicitly replaced by a new request.

### Optional message rates and complete one-shot observations — 2026-10-30

Global Work concurrency settings also expose `maxPeerMessagesPerMinute` (per
sender/recipient pair, including notify) and `maxPendingWatchesPerTarget`. Both
have default 0, meaning no count limit. Explicit positive values gate only new
requests atomically, before message/watch effects. Exact retries return their
receipts. Changing these limits neither retries rejected requests nor restarts
work. Automatic retained returns use their existing guarded inbox, not the
new-message rate check.

A root lifecycle event captures its status, workspace, time, and retained command
row high-water once. Every pending watch registered by then is considered using
64-row keyset pages; later registrations cannot consume that older event. Expiry
uses that captured time throughout. Each matching watch records one observation
and at most one guarded notification. Restart also reads watch receipts in bounded
pages. This preserves Stop, ownership, cancellation and unknown-input handling;
observation still neither starts the target nor proves successful task completion.

### Continuing-objective result delivery — 2026-10-19

New waits and reply boundaries capture a task continuation epoch separately
from its exact edit revision. Updating progress, references, constraints or
corrections within the same objective preserves this epoch. Creating or changing
the objective, pausing, blocking, resuming or completing it advances the epoch;
a pause followed by resume cannot revive an earlier notification. Existing
receipts without this field retain their exact-revision boundary. Migration
never retries suppressed, accepted or uncertain input.

New commissioned-task receipts also capture the requester's continuation epoch.
An already admitted member can return evidence in a later root generation after
the requester updates progress or commissions siblings within the same objective.
Creation preparation and initial input admission still require the exact captured
task revision. Existing receipts without the epoch keep that stricter boundary;
this change neither retries old refusals nor changes a blocked task to complete.

Ordinary automatic and idle-queued peer instructions also use their existing
host-captured reply continuation epoch for intake. Routine progress, reference or
constraint updates do not invalidate the same saved request; current constraints
and budgets still apply. This reuses existing receipt provenance without rewriting
it or retrying an accepted, uncertain or suppressed delivery. Legacy receipts
without the epoch, explicit anchored steering, commissioned initial input,
blocked-task intake and deliberate human resume retain their existing contracts.
Exact task edits still require their current edit revision. Notifications do not grant
authority: admission rechecks current task state, budgets, Stop/interruption,
assignment membership, ownership, permissions and any operation-specific
placement/cancellation boundary. Current constraints apply before model work.
The native adapter rechecks the same boundary at submission, including after
awaited host authorization. Receipt identity and outcome qualification still
prevent duplicate delivery and stale success. Retained results remain readable
when automatic admission is refused. Routine progress edits alone do not wake
coordination waiters; new results, changed authority or attention still do.

One-shot status/idle watches are explicit commands with an expiry and cancellation.
They observe only the named root's public lifecycle, do not load its history or
start it, and notify through the same bounded guarded inbox. Expiry is a visible
outcome, not approval or denial. Restart preserves pending watch receipts and
never replays a claimed notification. Idle means a root status boundary, not proof
that external effects or all background work have finished.

Peer inbox counts have no ceiling by default. Global Work concurrency settings
can optionally bound a destination’s pending inputs plus reserved reply slots,
pending inputs from one sender, and pending inputs from one assignment; 0 means
no count ceiling. Positive limits remain atomic admission checks, including
reserved returns, and never discard existing work. Admission gives the least
recently admitted pending sender the next turn using durable admission order,
with oldest pending input breaking ties. Sender selection considers the complete
pending set; only a bounded FIFO batch of that sender’s receipts is loaded.
Raising a limit resumes saved backpressured returns through the same guarded
inbox without replaying accepted or unknown operations. New queued instructions
expire after a caller-selected duration (default and maximum one day);
expiration remains an inspectable failed dependency without executing the input.
Accepted substantive inputs are never silently coalesced or dropped. Informational
notices still do not start execution; unsupported safe-boundary delivery remains
an adapter limitation rather than cancellation and resend. Host-retained causal
input lineage records the depth of peer-driven work for provenance and diagnosis;
its age does not impose a fixed-hop requirement for human intervention. A new
actual human input resets the recorded depth; observations, peer text and model
assertions of approval do not. Per-pair message rates, inbox capacity, assignment
and task limits, Stop, and exact input idempotency still govern admission. A
long-running delegated task can continue and return results within those limits.

### Busy recipient delivery — 2026-10-31

New ordinary peer messages default to `auto`. Host-created result notifications
also use automatic delivery. The same durable receipt is admitted at the next
supported native model-request boundary while the recipient is active, or as
ordinary input when idle. The native adapter checks the current host and native
generation immediately before admission. Explicit `queue` retains idle-only
intent; `notify` remains non-executing; explicit `steer` retains its exact saved
generation. Human-resume and dependency-block handling still wait for idle.

Only positive pre-submission no-effect refusals may return to queued, under the
same input ID. Existing lifecycle events reconsider them; there is no per-token
queue scan or retry timer. An admission, held input, or unknown effect is never
retargeted to a later generation. Native events may settle a receipt before the
transport acknowledgement. Stop, task, owner, configuration and budget checks
remain authoritative. Busy routing selects only eligible automatic receipts
before bounded payload reads, so deliberate idle-only messages cannot hide them.

On upgrade, only host-origin outcome notifications that are still queued, have
no submission evidence and match their canonical saved continuation claim become
automatic. Their original ID, payload and display attribution remain intact;
authored queue requests and uncertain or previously admitted notices do not change.

### Managed resource observations — 2026-10-14

The resource surface may project bounded, explicitly selected task records from
existing physical owners. The local Publisher owns static preview/site listeners,
OS-assigned ports, immutable release directories and stopped/interrupted receipts.
Operations owns recorded native process/kernel lifecycle evidence; a recorded
running state is an observation, not an independent process-liveness probe.
The projection must preserve these different evidence levels, omit commands,
output and environment, and never provision, replay, stop or reassign resources.
Each owner uses an indexed task-scoped page; inspecting one task must not load
other tasks or all retained history. Browser and agents use the same read action;
agents may inspect only their host-bound task, while users may select a workspace
task. Cooperative reservations remain separately labelled, with no inferred link
between a user-chosen key and a physical resource.

The initial managed inventory did not allocate dedicated scratch folders,
databases or browser profiles; the owned-storage addition below supplies those
specific local bytes. Dynamic application preview listeners remain undiscovered.
A documented working-files namespace alone is not provisioned isolation. Unknown operation cwd,
remote publishing state or resources outside these owners must remain explicit
coverage limits, never reported absent or safely isolated.

### Owned task storage — 2026-10-14 candidate addition

The workspace resource owner may explicitly provision a private task-owned scratch
directory, browser-profile directory or local SQLite database inside the chat's
actual execution folder (working directory, falling back to workspace). Routine
workspace descendants use existing file access without a configuration approval.
The private ownership ledger remains outside the workspace. It retains allocation intent before filesystem effects,
binds ownership to the authenticated root, bounds outstanding allocations and
records directory/file identity. Stable retries return the same allocation;
interrupted or replaced resources remain inspectable and are never recreated
from an uncertain receipt. Stop or changed placement before admission suppresses
unperformed effects; already-admitted local filesystem work may finish. Retirement
preserves contents and does not assert that external users of the files stopped.

Allocation does not change file-access settings or executable mount plans.
Inspection reports the current supported mounted file/patch policy for the
allocated path without warming a worker. Existing applicable access remains
usable; an agent allocation with denied, mixed or unobserved access is refused
before workspace effects. This includes newly created ancestors and ignore files.
Explicit user UI allocation does not warm a worker or certify agent access.
Unknown or custom tools never become an inferred grant. A browser-profile
directory is not a launched or confined browser, and a SQLite file is not a
provisioned database server. User and agent controls share ownership, receipts
and revision checks; peer workspace membership confers no allocation control.

New allocations use `.amplifier/task-storage/<root-hash>/<allocation-id>` inside
the captured execution folder, with a local `.gitignore` excluding their contents
from ordinary Git/DTU source snapshots. The user’s root ignore rules are unchanged.
The execution folder identity and path components are checked before effects;
symlink substitution or a conflicting ignore file cannot redirect allocation.
Managed checkout archive/restore retains the bytes and follows the exact existing
worktree owner's verified move records. Other path changes are unavailable, not
permission to recreate or relocate storage. Existing app-owned allocations keep
their original locations. Retries preserve the original placement even after a
chat moves. No files or pending permissions are migrated automatically.
The caller does not supply a destination. Nonretired allocation counts default
to 0 (no count ceiling), both per root and per app home. Optional positive limits
in global Advanced execution settings remain allocation-count limits, not disk
quotas. A stopped allocation owner is reconciled to unknown only
after its physical owner lock is free. Retirement releases a count slot but never
reclaims bytes. No automatic garbage collection or deletion action is supplied.
Read pages contain at most 32 records and do not hydrate conversation history.
The ready-worker policy check observes supported reference filesystem and patch
tools only; it does not certify custom tools, shell, browser or database access.

### Optional work and test execution counts — 2026-10-30

Global execution settings accept zero for all work counts: outstanding task chats
per source, environments per chat, pending checks per chat and installation,
retained resources and cooperative reservations per chat and installation. Each
defaults to zero (no count ceiling), as do local and registered target backend
running/retained instance counts. Queue wait also defaults to zero.
Saved positive operator values are preserved. Wait zero means until Stop or
cancellation; accepted checks retain their original deadline and resource budget.

These choices do not change same-request idempotency, causal depth, assignment
membership, authorization, Stop, retained ownership or physical resource
admission. Ordered checks retain their earlier-check/cleanup barrier; overlap
requires both checks to be explicitly independent. Unknown results remain unknown
and retain exact owners. Disabling a count cannot retire, replay or overwrite them.
Manual and queued environment allocations share the same atomic admission owner.
CPU, memory and disk budgets remain enforced by their existing machine/backend
owners where supported. Bounded display pages and record sizes are not
concurrency ceilings. A default change never rewrites explicit saved limits.

### Notification display provenance — 2026-10-14

A retained versioned notification is labelled as an Amplifier update when its
exact command, payload binding, original request and per-revision wait claim
match. This covers negative reports, observations, preparation failures and
older successful revisions after a later report. It establishes authorship, not
current qualification, permission or successful dependency completion. Same-root
preparation updates require the same exact system claim; arbitrary self-messages
and imported or prose-only attribution remain unverified. Reads use only the
requested page's bounded command identities and never open the producing chat.

### Return recaps and portable phase observations — 2026-10-14

Explicit recap and diagnostic reads reuse existing request, reply, observation,
resource and native runtime records. They do not load peer transcripts, call a
model, admit work, replay effects or establish execution/configuration authority.
Agents inspect their bound root; users may inspect a selected root. Workspace
and actor scope is rechecked after detached owner reads. Reads remain bounded
and indexed even when many dormant conversations exist.

A recap compares an optional caller-held cursor with current bounded facts.
The cursor stores only version signatures and scope, not text or drafts. It is
untrusted presentation state, never evidence of completion or authorization.
Missing, malformed, foreign or obsolete cursors reset explicitly. Reports use
existing names and declared/observed outcomes; reported success is distinguished
from terminal qualification. Page gaps remain explicit. Only displayed changes
advance the returned cursor; further changes stay available on the next read.
Browser cursors are private to their tab and conversation and advance only after
the returned view is visible. Hidden, cancelled and stale responses must not mark
changes seen. Other clients may maintain independent cursors through the same API.

Portable diagnostics distinguish admission queue timing, retained preparation,
reply/claim phases, observed native provider/bridge timing and sampled host loop
delay. Correlation uses hashed actor and operation identities; arbitrary content,
paths, titles, tool arguments, credentials and private drafts are excluded.
Browser action/request timing is attached locally, never synchronized as shared
conversation state. Inclusive measurements may overlap and must not be summed.
The browser may also attach a bounded, numeric recap timing sample from dispatch
response delivery to the component through its React layout effect after DOM
mutations commit. This monotonic-clock observation is local to that recap mount;
it excludes network delivery, physical paint and global application rendering.
Unavailable samples and unmeasured paint remain explicit; a recorded
phase or timing is not proof of correctness, live liveness or external effects.


### Chat write override reset

The file-access controls described in [configuration authority](../configuration-authority.md#chat-write-overrides)
use canonical native session settings. Save/reset share UI and agent actions,
require the matching preview revision and existing file_access/session authority
for agents, and retain native child ceilings. Reset removes only the session's
filesystem write allow/deny override keys. Shared workspace settings, read policy
and other settings are preserved. Inherited configuration changes invalidate the
preview; saved changes reach the existing runtime refresh boundary before the
next input, with no replay or unsolicited turn.


### Ordinary file reads and search

Read authority is independent of write roots and configuration administration.
Unified supplies an explicit unrestricted read policy when no file/search read
boundary is configured; reading installed package source requires no
`file_access` or `runtime_configuration` grant. Filesystem `allowed_read_paths`
and search `allowed_paths` support unrestricted null and restrictive lists,
including empty deny-all lists. The standalone search module retains its omitted
configuration default of the working directory; Unified deliberately supplies
null instead. Explicit module/per-tool search declarations and filesystem read
ceilings intersect across root, child, spawn, overlay and retained plans; denials
accumulate. Resuming a child cannot widen its saved explicit read boundary.

The reference reader, grep and glob check denied paths, including filesystem
aliases, before exposing contents or names. Grep chooses its Python engine when
explicit denials exist so ripgrep cannot implicitly open denied ignore files.
This policy mode uses Python regex semantics and may be slower. Unrestricted
search retains ripgrep; restricted search uses explicit tool exclusions rather
than ambient ignore files. These controls do not grant writes, widen scoped
history APIs, expose private drafts, or establish shell/OS isolation.

Root and child mounting reject reference modules that cannot honor the selected
read policy before model input. The same check runs in existing no-model runtime
qualification. Dependency updates use the existing checked staging/activation
path; no capability approval or automatic replay repairs a recorded old worker.


### Explicit review of completion notifications — 2026-10-22

A requester may acknowledge an exact outcome after reviewing it through
`coordination.acknowledge`, using the current result's revision and review key.
This is the caller's explicit disposition, not a claim that a passive read was
consumed by a model. Reads, UI views and assistant prose do not acknowledge.

The host retains the acknowledgement in the existing command journal, checks the
current root, original requester/workspace, task continuation epoch, assignment
and Stop boundary, and atomically suppresses only the matching still-queued
notification. An already submitting, admitted or unknown notification is kept
unchanged. If backpressure prevented enqueueing, the same reviewed outcome does
not enqueue later. A changed outcome remains eligible for its own notification.
Results, declarations, successful qualification, task status, budgets and
permissions are unchanged; acknowledging never cancels work or permits replay.
A stable command identity recovers a lost acknowledgement without repeating an
effect. This action is available on the explicit recovery/report surface.

### Compact assignment orientation

`assignment.brief` reads current member metadata and the caller's latest direct
request to each member, with exact inspection/result handles. It does not load
conversation history, admit a dormant worker, change task authority or create
another task/context store. Shared Work and original constraints remain authoritative.
A short objective/result preview is labeled when truncated; idle is not completion,
and a response's qualification is reported separately from delivery.

Each page is at most 9 KiB UTF-8, defaults to eight members and accepts `limit`
1–16. Follow `nextAfter` for further members; current progress can change between
pages, while assignment membership changes invalidate the cursor. Unavailable,
detached or moved members expose no title, task or request preview. This is a
current orientation view, not a global snapshot, history search or instruction
supersession mechanism.

The brief reads directory metadata without admitting dormant members. Task text
comes only from already resident fields or bounded scalar projections of existing
retained runtime/session records; absent projections stay unknown. It does not
open native control files or decode saved view/history bodies to fill a preview.
The agent bridge also avoids unrelated pending-progress publication for this read.


### Fresh corrections to completed assignment members

A new direct queued (including automatic idle delivery) request from the current
active assignment owner to a bound completed member is ordinary commissioned
work within existing authority. It needs no fresh target human message or resume
proof. Stop, paused/cancelled tasks, nonowner peers, membership changes, native
ownership, current task identity and explicit budget refusals still apply. Host
receipts bind the completed task and new input; old requests are never replayed.

The native owner reserves that input's turn durably without reopening the
completed objective. It may correct/report directly or create correction objectives
through `task.create`. Successor attribution comes from the actual native input
and generation, not model-supplied predecessor IDs. Its initial allowance and used
turns are inherited; the prior completed record and evidence remain in canonical
history. Exact request reporting follows those objectives within the same admitted input;
additional task creation retains the original correction attribution and allowance.
A genuinely fresh input retains normal task creation. No old input is admitted
again and unrelated later objectives cannot report against the earlier request. Assignment cost/token usage
continues to come from existing execution receipts, not task-history counters.

A reserved successor requires producer `goal.reserved_turn_accounting = 1`.
The synchronous `goal.turn_already_charged` witness verifies current task, native
input/generation, reserved count and continuation ordinal idempotently. The
producer counts later automatic turns normally; errors do not refund or recount
a reservation. An unsupported producer still permits direct correction/reporting
but refuses successor creation before mutation. No new continuation loop exists.
The current task retains the exact bound goal progress, including after terminal
goal removal. Checkpoint/restore therefore preserves spent turns and selected
caps; replaced goals and failed mutations retain exact task identity/revision.

The existing authorized `assignment.task.setLimit` action may also revise a
completed member's limit/correction while preserving its completed status,
evidence, history and used turns. Ordinary completed task edits remain refused;
this control sends no input and does not restart work.
