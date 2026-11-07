# Configuration authority

Configuration administration uses the existing shared actions, command receipts,
approval cards and settings owners. A root can request named effects and scopes;
the user approves once, and the root can reuse that grant across later turns.
The user can also issue the same grant directly through `capability.grant`.
No extra settings form is required for the agent. Direct user administration and
ordinary own-chat selection of an existing provider/model keep their existing
paths.

`capability.read` inspects grants without executing anything. In Advanced >
Model & tools, **Agent permissions for settings** shows the same records and
offers revision-checked revocation. `capability.request` uses the ordinary
Allow/Deny card. Native root `app_control` registers a waiter in the existing
worker approval map before publishing that card; the tool remains pending until
the decision, cancellation or expiry. The committed decision wakes that tool,
which reads current authority before returning. The agent must then deliberately
perform its next action. Approval never replays a previously rejected write.

Raw API requests are nonblocking and return a pending receipt. After a worker is
lost, approval still saves the decision but does not revive or replay its turn.
The root can read/reuse the authority on a later explicit input. Already active
grants are reusable; natural turn completion does not revoke them. Stop,
workspace change, explicit revocation or optional expiry invalidates them.

## Chat write overrides

**Files this chat can edit** uses the canonical native session settings layer:
global, project, workspace-local, then session. `permissions.get` with an explicit
`sessionId` and `scope: session` previews the current write defaults and the
workspace defaults that would be inherited after reset. `permissions.save` at
that scope and `permissions.reset` require the returned `expectedRevision`.
The revision binds the native session, execution folder, session settings and
inherited configuration. Re-read after any stale-preview rejection.

Reset removes only `allowed_write_paths` and `denied_write_paths` from this
session's `overrides.tool-filesystem.config`. It preserves read policy, other
tool settings, other chats, and shared project/local settings. An already
inherited policy is a no-op; reset does not create a session settings file.
Empty ancestors made empty by removal are pruned. Other session declarations,
bundle restrictions and child ceilings remain effective; the preview describes
saved defaults, not a claim that every mounted tool has that access. Shell
policies are separate. The existing **Project file access** form is shared by
chats in that workspace and is not reset by this control.

Execution wrappers must forward hook-owned tool policy to the actual executing
tool. Reading a delegate's attributes while shadowing policy writes on the
wrapper leaves approvals disconnected. Work keeps its own execution identity
and checks; tool approval callbacks retain their ordinary allow/deny behavior.

Save and reset both require `file_access` at `session` scope for agents, including
when reset restores wider inherited access. Direct user controls use the same
action. Native children cannot administer the root or bypass inherited ceilings.
The canonical locked settings mutation rechecks authorization and the preview.
Command receipts make an exact retry idempotent. A changed policy invalidates
only this chat's configuration through the existing runtime owner; active work
and persistent children reach their existing safe boundary before remounting.
Before the canonical file write, the existing host record durably fences new
input with a pending file-access change. New root/worker/voice execution is rejected,
queued peer input waits, and schedules/observations recheck their existing
admission guards. Human messages to the current active root generation remain
available as steering. They cannot fall forward into a new turn if that run ends
before delivery; an undelivered message remains saved with its delivery state.
Answers, approvals and Stop remain available for existing work.
The UI explains pending access and how to finish or stop existing child work.
Only confirmed retirement, or proof that no owned mount exists, clears the fence.
After host restart, new mounts read canonical settings; no old worker is adopted.
A failed write or lost acknowledgment can conservatively retire the old mount,
but cannot silently continue it with a missing fence. No-op reset adds no fence.
No worker or provider turn is started by this control.

The browser matches terminal action status to the accepted command ID. If
another tab replaces that status, it reconciles once through the existing exact
`/managementResults/<commandId>` JSON Pointer read, without fetching command
history. Missing receipts, interrupted requests or unavailable reads become an
explicit unconfirmed result after a bounded wait. Entries remain editable, but
save/reset require **Reload preview** to refresh the current policy and revision;
that recovery preserves entered values and never repeats the prior mutation.

## Effect and ownership mapping

| Effect | Shared routes | Scope |
| --- | --- | --- |
| File access | `permissions.save`, `permissions.reset` | Explicit session, local, project or global for save; session only for reset |
| Connections | Provider save/remove/move/reorder/login | Explicit local, project or global; connection sources can execute code and credentials can change |
| Model defaults | Provider finish-setup/image defaults, routing use/save | Explicit local, project or global |
| Modules | Module and source save/remove/validate | Explicit local, project or global; validation can execute code |
| Bundles | Registered bundle add/toggle/remove/move/reorder/save, bundle default | Global registered storage; workspace default is local |
| Bundles | Bundle switch/fork | Session |
| Runtime configuration | Configuration apply, runtime configuration apply/toggle | Session |
| Connections | Runtime configuration provider login | Session |
| App defaults | Settings update and runtime retention update | Global; a settings bundle field also requires the bundle effect |

Session grants belong to one actual native root, with its workspace and
interruption revision. Shared host dispatch validates the principal and current
generation. Supplying a root session ID in model arguments does not confer that
identity. Explicit global configuration targets work for an authorized root;
non-global grants do not write another workspace's settings.

A lower-authority peer request cannot borrow a recipient's grant. The host
intersects current sender/recipient grants through bounded retained input
lineage. Revoking a sender also prevents a queued recipient effect. Host-created
returns to the root's own saved dependency preserve that root's original
authority and original input lineage. A human may explicitly approve the exact
current peer request. Peer-authored instructions or reply IDs do not constitute
approval.

## Consumer audit

| Consumer | Implemented boundary and evidence |
| --- | --- |
| Native root | Actual mounted `app_control` can request, wait, resume and perform a granted global write; unsupported/absent/stale principal or generation is denied. |
| Finite and resumed children | Core/Foundation-mounted children carry their own principal. Parent grant borrowing is denied on first and resumed execution. |
| Persistent and nested children | Same native bridge installation; children and grandchildren cannot inherit root grant identity. |
| Recipe child | Tested the shared in-process recipe-compatible child spawn seam. The optional full recipe module was not mounted. Subprocess recipe execution remains unsupported by the existing adapter. |
| Commissioned root | Existing session/configuration creation does not copy grants, which remain session-keyed command records. Independently explicit user administration remains available. |
| New-task schedule | Actual schedule/session-creation fixture creates a new root with configuration but no grant; its attempted configuration write is denied. Assignment membership does not transfer grants. |
| Same-root schedule | Scheduling retains its independent source authorization. A later root configuration action still checks that root's grant, expiry and Stop revision; scheduler-origin dispatch has no authority bypass. This is source/shared-route coverage, not a real-provider schedule run. |
| Background children | Retain native child principal; no root grant inheritance. |
| Background management | Captured authority follows the existing task/ContextVar path. Queue, validation and runtime-preparation waits recheck it before supported effects. Provider login activation uses guarded settings/credential sinks. |
| Queued configuration | Existing pending configuration retains grant/revision provenance across host restart. Revoked/stopped/expired work is marked failed and its requested plan is preserved; nothing is replayed. |
| Direct dispatch and attached clients | Shared service/app bridge/per-session HTTP routes expose the same actions and scope checks. HTTP grant, exact retry and scoped revoke are covered. |
| User settings UI | Existing user administration remains available without an agent grant. Browser approval/read/revoke uses the shared actions and preserves private drafts. |

The implementation has no second permission database or transcript. Grants,
approval decisions and exact command retries use existing SQLite command
receipts. Reads are paginated and active/pending grants are bounded per root.
Historical expired/revoked grants do not exhaust a lifetime quota.

## Delayed effects and limits

Configuration actions retain the selected grant revision and interruption
revision. The host checks them after waits, before guarded settings writes,
credential writes, executable validation admission and runtime-control admission.
Settings writes recheck their concrete scope and workspace inside the canonical
mutator. Already admitted validators, external authentication processes or native
controls can finish; revocation cannot undo those effects. Activation after an
awaited login is still checked. Passive grant reads never execute pending work.

These effects name administration domains, not mutually isolated privileges.
Module settings include provider configuration; bundle and mount-plan changes
can replace tools or providers and alter file-tool policy. Executable sources can
run code with the worker's ambient access. App defaults include update automation.
The approval labels disclose those broad effects. A narrow model-default grant
does not authorize arbitrary provider configuration: those routes preserve the
existing provider settings, and routing values reject credential material.

This is configuration control authority, not an operating-system sandbox.
Arbitrary shell programs and external modules can bypass supported settings
APIs. General update/install/deploy authority and real-time provider effects are
not covered by this grant slice. Neither a root grant nor assignment membership
grants stop/steer/settings authority over an independent peer.

## Ordinary file reads

Ordinary `read_file`, `grep` and `glob` calls do not need configuration grants.
Write roots do not define a read boundary. Explicit configured read limits and
denials still apply; an agent must not change configuration or switch tools to
avoid a deliberate denial. An old runtime module that cannot enforce the selected
policy needs the existing component update path, not broader administration.

## Read-history audit: separate authority

Configuration grants do not grant or restrict history access. The authenticated
same-user app/tool capability supplies access to the registered own-history
library. A separate [read-scope resolver](owned-history-scope.md) applies explicit
workspace/all selection to history, recall and coordination reads. No read grant
database or per-read approval was introduced. Request results, output libraries,
user memory and mutation authority keep their independent narrower rules.
Cross-account or multi-tenant isolation is not established by this local work.

## Qualification boundary

Configuration attempts bind to the native worker identity reported on its private
startup pipe, including the native session ID. A managed launcher such as `uv`
can have a different PID. Missing or invalid worker identity refuses before
dispatch and leaves a known unperformed operation, rather than holding input on
an unknown outcome. This identity stays out of public runtime reports; original
grant, target, interruption and native receipt checks remain in force.

The maintained queued-configuration checks include actual `uv` launcher/worker
separation, successful settings application/remount, and missing or invalid
identity refusal without a settings write or unresolved input fence. Runtime
checks reject identity from another native session. Run these affected checks
with `uv` installed; a direct-Python fixture alone does not cover that boundary.

Tests include positive user and agent administration, denied effects/scopes,
revocation while queued or preparing, durable approvals/restart, exact retries,
lost acknowledgments, Stop, peer lineage, and new scheduled roots. Native tests
mount Core/Foundation and the real app-control tool; their orchestrator is
scripted, without a model/provider call. The browser uses production assets and
the actual service with a controlled runtime. It covers Allow, grant reuse,
revoke, reload, retained private drafts and a narrow layout. The native waiter is
qualified separately from the scripted browser adapter.

Source, native, browser and installed-wheel evidence are distinct. These checks
do not establish real-provider cooperation, provider-login acceptance, optional
recipe integration, release publication, installed-host adoption or OS isolation.

## Persistent Full access

### Unattended Full access direction

Accepted direction, October 30, 2026: **Full access** is standing operator
authorization for all supported agent capabilities, with no recurring human
approval gates. It covers files, tools, configuration, delegation, collaboration,
task and resource control, schedules, recovery, cleanup and outbound work such as
issues, reviews, merges, releases and publication. Trusted authenticated systems
can initiate or delegate work without a human conversation or connected client.
Ask when needed remains available.

The operator opts in through trusted UI, CLI or authenticated API controls.
Retrieved content, repository settings and an agent's assertion of permission
cannot enable Full access. App, workspace and chat scopes select which work
receives standing authority; they are not per-effect approval checkpoints. Native
descendants, commissioned tasks and scheduled continuations inherit through
their existing trusted source relationships, rather than copied permission flags.
Explicit narrower preferences and revocation remain effective. Source-derived
authority is rechecked at existing effect boundaries.

Full work must not require a fresh human message, original participant or manager
approval, attached browser, or fabricated human answer. Autonomous choices retain
agent/system attribution. Missing factual inputs can be reported without turning
them into permission requests. Actual Stop fences the stopped work chain; that
chain cannot resume itself. A fresh trusted operator or system request may start
new work. The saved access preference persists until explicitly changed.

Native execution identity, target ownership, cancellation, idempotence and truthful
unknown-effect recovery remain correctness requirements. Full access does not
manufacture OS privileges, account credentials, unavailable tools, independent
review or success. Reuse existing access records, work relationships and command
receipts; do not create a second grant service or approval process.

This is the implementation destination, not a claim that every current action
already supports it. The existing behavior and command-line entry points below
are narrower: task lineage, system-origin action parity and action-specific human
gates require incremental qualification before being described as complete.

Global model-capacity waiting settings accept standing Full from an explicit
calling conversation, including its current native descendants and trusted host
systems. The existing policy owner checks authority at commit and retains exact
source-bound receipts without a new approval. This adjusts waiting capacity, not
spending limits; [the execution contract](contracts/execution.v2-candidate.md)
defines its lifetime, cancellation and observation behavior.

Initial feedback publication and direct text follow-ups have a Full path for
current agents, assigned native children and trusted host systems with an explicit
calling conversation. Initial files and prepared excerpts use conversation-owned
staging without a browser or renewed human confirmation. Existing receipts retain
actual caller/source authority, verified recipient facts and immutable content;
every external write rechecks the captured Full revision and Stop boundary.
Read-only reconciliation never renews effect authority or repeats uncertain work.
Later-file additions use the same conversation-owned Full path and exact file
manifest with optional review; Ask retains browser review and confirmation.
Optional text comparison reviews retain their separate client workflow. The precise behavior and limits live in
[the feedback contract](../feedback-excerpts.md#initial-publication-under-full-access).

### Implemented standing preference

Chat info > **Access & approvals** offers **Ask when needed** and **Full access**.
The user chooses this chat, workspace default, or app default; chat overrides
workspace, which overrides app. Inherit removes that layer's choice. Defaults
remain Ask when needed. Full access persists across turns, Stop and host restarts.
Only authenticated user controls can call `access.set`; agents can inspect their
own mode with `access.get` but cannot enable it. Repository settings cannot opt in.
Records live in the existing host command database with revision-checked,
idempotent saves. New native workers read exact policy and task-source records.

Commissioned, new-task scheduled and Full independent-handoff roots inherit
through their retained creator, scheduled-origin or exact accepted handoff
relationship. The scheduler's validated source/run relationship
is saved with the new root, before its first native preparation. The receiving
chat's explicit choice wins.
Otherwise a source chat's specific choice follows the task ahead of workspace
and app defaults. At a cross-workspace relationship, an explicit destination
workspace choice wins before source inheritance. Removing a chat override restores
this inheritance; it does not copy Full access into the receiving chat.
Missing, cyclic or ambiguous source relationships, including unavailable or
invalid committed source payloads, fall back to the receiving
chat's independently applicable workspace/app preference, or Ask by default.
They cannot supply inherited Full or erase an operator's existing broad Full
choice. The unavailable relationship remains in the revision and source diagnostic.

Host checks and native file preparation use one resolver. It reads only the
requested owners and their source chain, following each committed manifest's
exact immutable view payload and immediately projecting its identity/source
fields. It never follows cold message/execution references or trusts mutable
`view.json` presentation. Read cost grows with source depth and those owner
payload bytes, not unrelated history. Directory rows locate aliases but never
supply authority. A worker's host-owner ID is transported separately from its
native ID, so older aliases do not change its policy. The effective revision binds
consulted choices, relationships and source Stop revisions. Source revocation or
Stop fences an already captured derived effect; an explicit receiving-chat choice
is independent of source permission changes. The saved preference persists after
Stop, while execution admission still owns cancellation and resume.

This source resolver covers independent task roots. Qualified native children can
use their owning chat's Full mode for the existing shared configuration effects
and inspect that chat's `access.get` and `capability.read`. Configuration routing
uses the existing effect requirement; it does not maintain another action list.
The source check explicitly opts into the native child's current assigned
generation and captured Stop boundary. Finite, nested, resumed and persistent
mounts keep their actual principal and original assigned inputs. They do not
impersonate the root or borrow an unrelated later input. New Full configuration
receipts retain actor, owner, origin, generation and all available assigned-input
IDs separately. A missing or empty native input envelope records unavailable input
coverage, not a verified empty assignment; its cause and count are not inferred
from the root's later inputs. This observation adds no permission gate under Full.
Accepted delayed work keeps its captured Full revision and the existing final
effect checks; the finite child finishing does not itself revoke accepted work.

Ask-mode discrete grants remain root-owned, and children cannot grant/request/revoke
those root grants or change access mode. Independent chat handoffs also accept qualified Full descendants and explicit
trusted systems, retaining the actual input origin and actor; they use the
[detailed handoff contract](../clients/new-chat-handoff.md). Other collaboration
consumers keep their strict root default. Existing child file-policy composition is unchanged.
The configuration operation and patch-placement owners accept qualified Full
descendants through this same source check. They validate the calling conversation
themselves. A Full patch bind retains the explicit `bind-to-execution` choice,
exact observed placement/configuration revisions and original effect authority;
it requires no human `sourceMessageId`. Ask retains its existing human-choice and
discrete-grant behavior. Passive root observations and exact retained binding
receipts remain readable without admitting another effect.

Fresh trusted system configuration requests may use an explicit calling owner and
current Full without inventing a native principal, generation or human input.
They retain system attribution and unavailable input coverage. A system request
that carries a native binding still has to validate that binding. Saved mode,
Stop and target checks remain effective at the delayed configuration owner.

Managed test-host setup, its retained reconciliation and interrupted-setup cleanup
also accept qualified Full descendants and trusted system callers. Their existing
conversation/workspace intent owns the result; the original actor and authority
remain recorded. Accepted background work can outlive a turn, with current Full,
Stop, placement and resource checks before effects and final publication. Routine
environment start, execution, stopping, reconciliation, artifact export and owned
cleanup likewise accept current Full native descendants and explicit trusted
system callers. Accepted operations retain the captured Full/Stop boundary while
the conversation's canonical execution identity continues to own the resource and
request fingerprint. New Full request receipts record the actual caller
separately; exact retries preserve original attribution and never repeat an
uncertain effect. Passive removal inspection keeps its bounded cold-owner read
path. See [test environments](test-environments.md) for this domain's exact scope;
this does not expand generic task-resource or queued-check admission.
Work and provider admission use their respective canonical owners; this consumer
adds no duplicate permission policy.

Full access preauthorizes configuration effects at all scopes and ordinary
allow/deny tool permissions. Pending valid permission requests are answered once;
questions, expired requests, lost workers and old tool effects are never replayed.
For Work-associated execution it also accepts partial or inferred Work context:
missing fitted-content proof does not add a permission gate or restrict the set
of supported actions. The shared Work policy reuses current native generation and
Stop checks for roots and trusted descendants, preserving their actual identities.
Coverage remains explicitly unknown or partial when not established; it is not
verification or success. Action owners still enforce their own factual inputs,
revisions, receipts and recovery. This does not establish descendant support or
complete deferred-effect cancellation in every individual action owner.
Existing conversation ownership, execution identity, Stop, resource ownership, capacity,
retention and recovery checks remain in force. A trusted root remains responsible
for evaluating peer messages. Configuration commits recheck the captured mode
revision; turning the mode off fences delayed changes and grants derived from it.

A failed configuration operation retains its original authority, including Full
access. If its exact authenticated terminal native receipt proves that rejection
occurred before any write, the error path records that no-effect result and
releases only that operation's input fence. The original error, request and
history remain; no settings or conversation input are retried. Missing, invalid,
nonterminal or partial evidence keeps the operation held. Redacted credentials
remain bound to the module's source identity; a changed source requires a fresh
inspection rather than copying hidden values from an older full plan.
The agent can reconcile native evidence and explicitly resume proven
no-effect work without another permission request. Recovery checks the original
mode revision, Stop boundary, target and exact payload again; re-enabling Full
access does not renew an older operation. Unknown or partial effects cannot resume.
A terminal sealed native save receipt and an unchanged saved plan allow explicit
resume to finish that same operation's loaded readback. It observes the exact
existing worker first; only an absent worker may mount the proven saved plan.
This continuation never repeats the settings write or submits conversation input.
A different loaded plan remains held. Readback may equate relocated module source
declarations through qualified generations and matching recorded repository,
revision, subdirectory and source-content digest. If an ecosystem update has
already been adopted, an older saved declaration may also resolve to the worker's
captured qualified generation for the same package, repository, mutable ref and
subdirectory. The worker's derived profile must match the adopted parent's exact
revision and content digest. A later global component promotion does not invalidate
the existing worker's captured graph. This recognizes an existing update, never authorizes
a new source choice or moves a fixed revision/tag. Module configuration, ordering
and disabled rows remain exact. Retained effective
configuration files are not loaded-worker evidence. Missing provenance fails closed.
While recovery is held, `configuration.inspect` may observe the exact existing
worker and publish its actual loaded plan, even when it differs from the saved
request. This read never starts a worker, changes settings or settles the operation;
Stop or replacement of the observed worker prevents stale publication.
For UI, user or internal originals, a current Full agent, qualified child or
trusted system can reconcile and resume through the same recovery owner. Ask
cannot substitute a new grant for that operator-origin authority. Resume retains
the current Full authority in the operation's existing resumptions, checked at
queued application and native write admission. Revoked original agent authority,
revoked resumptions, Stop and changed targets cannot be renewed by another resume.
The original request, hash and existing history remain intact. Explicit recovery
transitions append their actual caller origin, conversation owner and observed
native principal/generation/input coverage to the existing private history; they
also record the Full revision used for operator-origin recovery. Public operation
views omit this attribution. Recovery does not relabel the original actor or
replace the retained effect authority.

For an already executing Full native child, the exact operation read, reconcile
and resume calls can reach the recovery owner even when that same settled pending
operation blocks ordinary child execution. This route authenticates the current
process and assigned run, Stop and execution/configuration revisions, task and
assignment controls, current Full and the operation's own fence. The recovery
owner still checks the original authority, target, revision and sealed attempt;
unknown effects never become permission to replay. Ordinary child tools remain
blocked. This qualifies native tool routing, not a fresh model turn: provider and
capacity admission remain unchanged, and scripted native tests do not establish
that a blocked child can obtain model capacity to decide to recover.

File, patch, image and search mounts (including child definitions and restored
plans) use all accessible local filesystem roots under Full access. Original
policy declarations are retained in the plan and restored before recomposition;
turning Full access off cannot preserve a widened snapshot by accident. Existing
worker retirement follows the file-access admission fence and waits for active
work and persistent children. Approvals use the saved preference immediately; the
UI separately identifies file mounts pending retirement. No provider turn starts.
Operating system permissions, unavailable tools, external account authentication
and explicit user questions are not replaced by this preference.

### Command-line access

`amplifier-unified access full` accepts the risks of Full access and saves the app
default without asking again or starting a task. `amplifier-unified access` shows
the saved/effective mode; `amplifier-unified access ask` changes the app default
back. Explicit chat/workspace choices continue to override that default. Use
`access status --scope session --session ID` to inspect a particular chat, or
`access inherit --scope session --session ID` to remove its override. Workspace
scope likewise names a chat in that workspace. `--json` returns the shared action
result for automation. No browser or human message is needed.

`amplifier-unified run --yolo "Do the work"` saves Full access for that task before
provider/tool setup and execution. `--access full` is equivalent; `--access ask`
and `--access inherit` change or remove that task's saved choice. These options
also apply to `continue` and `tool`, including an explicit `--resume ID`. The
choice persists for the target task; it is not silently reverted when the command
exits. All paths use the existing authenticated, revision-checked access actions.


### Global access and routine testing

Settings > Privacy & files > **Access & approvals** sets the app-wide default
without opening or creating a chat. It applies to existing and future chats in
all workspaces on this host; explicit chat or workspace overrides still win.
The shared `access.get` and `access.set` actions accept `scope: "app"` without a
`sessionId` for this user-only settings path. Saves retain revision and command-ID
checks. Agents cannot enable Full access.

Routine managed test snapshot, preparation, execution, export and owned cleanup
are task-authorized workspace work, including in Ask when needed mode. They retain
owner, generation admission, Stop, assignment, placement and backend identity
checks. Full accepted work can outlive its originating turn while its captured
authority remains valid. Ask-mode host setup and broader configuration
administration keep their separate grants.

Pending capability cards are retired after Stop, workspace change or expiry, and
native waits expire after host replacement. Legacy test-only cards also retire.
Retirement preserves receipts and history, never grants permission or starts work.
A stale Allow/Deny click acknowledges the expired request with `decisionApplied:
false` instead of trapping the user in an error. Raw API configuration requests
without a native waiter may still survive a host restart when otherwise valid.

Retired cards show the observed reason and **Dismiss old request**, rather than
unusable decision buttons. Dismissal preserves the exact request in
**Permissions history**, opened on demand from chat in the Canvas overview.
Pending decisions and undismissed old requests remain in chat. History is read-only;
opening or closing it does not change permissions or restart work.
Pending counts still derive from current pending records;
an old request is not an outstanding approval. An already-allowed grant is never
revoked merely because a lagging card still looks pending after restart.

**Review current scope** reads `capability.read` with the exact request ID.
It shows applicable saved authority and Full access separately from request
expiry. It neither opens runtime settings nor sends an input. The optional `id`
read returns a request projection with `requestHash`, revision and the available
decisions. Bundled decision controls check that hash before submitting and the
server rechecks it under its commit lock. A failed decision refreshes display
without repeating the mutation or changing the proposed scope silently.

Bundled cards fence decisions as soon as this page requests Stop, before its
command can wait behind Canvas work. A lost Stop acknowledgement stays unknown.
The page may passively read that exact command from the host's existing command
ledger, bound to its session and originating client. Confirmed acceptance releases
only that command's local fence; it does not prove runtime termination, replay
Stop, or send a decision. Missing or mismatched evidence keeps the fence.
A preflight begun while fenced stays invalid after reconciliation: the person must
review and decide again, with their note retained. This ordering fence is local
to the mounted page, not a durable unresolved-Stop journal across reload.

Dismissal reuses `capability.revoke` with `dismissRequest:true`, the observed
`expectedRevision` and `expectedRequestHash`. It refuses active/current requests.
An unavailable receipt permits only dismissal of its exact retained card, not a
fabricated grant or a claim that an earlier operation did not occur. A native
waiter may receive one bare Deny; history-only notes are not delivered. Missing or
lost notification acknowledgement stays `unconfirmed` and is not retried.
Dismissal never renews authority, starts a worker, undoes effects or replays work.

Both Allow and Deny accept an optional `comment` (up to 4,000 characters). The
host retains it with the decision and in chat history. A live native waiter
receives the note in model context before its tool is released; configuration
requests also return it in their canonical grant receipt. Notes on expired
requests are retained only. They never revive an old waiter or start a turn.
Choose Deny when the proposed operation itself needs to change; an Allow note
is guidance for subsequent work, not a rewrite of the operation being approved.

### Owner-local scheduled work

Full roots, qualified native children and explicitly identified trusted system
callers may create, update, normally resume and reconcile their own schedules
without another human message. Schedules captures the shared Full source once,
before preparation waits. Existing schedule/run receipts retain actual actor
provenance and exact source access, Stop, native owner and placement witnesses;
no new authority store is introduced. Accepted recurring work outlives its
authorizing generation and process. Due conversation creation, task-control
admission and scheduled input admission recheck the captured authority. Full
off/on never renews it. Exact completed retries remain observational.

Ask keeps its existing human-message authorization and evidence rules. Full
reconciliation labels the current actor's finding, never a fabricated human
source or verified outcome, and cannot resume or replay unknown work. This
owner-local path does not widen cross-root repair handoffs or authorize stopped
native work to rearm itself. Fresh future-schedule repair is governed separately
by [automation recovery](../contracts/automation-overview.md).
