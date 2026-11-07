# State, storage and working sets

Design supporting [CS](../../contracts/client-state.v1.md),
[HP](../../contracts/host-protocol.v1.md) and [WS](../../contracts/working-set.v1.md).
This describes the target, not completed changes to current persistence.

## Conversation, participation and execution

The experience owns a **conversation/session**: its shared transcript, participants
and delivery facts. This may be the Unified conversation experience, Slack,
Minecraft or another experience. A browser is a view into that owner, not the only
place its conversation exists. A workspace groups conversations and shared
context/files; it does not prescribe a work scheduler or agent workflow.

An **agent instance** participates through a connector/adapter. It may join several
conversations, and a conversation may include several agents and people. The
instance/runtime/adapter chooses its memory, working context and optional copies.
It can retrieve connected conversations when useful. Neither one private context
per relationship nor one global model history is a platform requirement.

An **execution binding** identifies the native engine/session and its exclusive
writer. It is not the shared conversation ID. Existing native history remains
engine-bound; adding another participant does not convert it. Agents respond to
their supported connection/message/event triggers and may deliver through chat,
external work, or both. A software factory can implement that boundary without
exposing its internal harness. [HP7](../../contracts/host-protocol.v1.md) and
[AP3](../../contracts/agent-protocol.v1.md) own the observable promises.

```mermaid
flowchart LR
    UI[Web / terminal / native views] <--> B[Broker: routing and admission references]
    B <--> E[Experience: conversation and participants]
    E <--> C[Connector: agent participation]
    C <-->|ACP execution where supported| A[Agent instance / runtime / factory]
    A --> M[Its own working context and memory]
    B --> I[Rebuildable paged metadata catalog]
    E -. metadata changes .-> I
    B --> L[Existing process and resource lifecycle owners]
    L -. wake / settle / retire .-> A
    E --> T[Shared transcript and resource references]
```

These are responsibilities, not seven new services. The host may carry the ACP
transport while the connector owns its experience-specific mapping. Shared host
records contain locators, small summaries, routing/authority references and
admission outcomes; they do not mirror conversations or inspect model histories.
Relaying requested content still requires transport encoding. Moving a full scan
to another process does not remove that scan: measure broker and owner costs.

For legacy sessions, an experience adapter exposes the existing authorized history
view without rewriting native files or making a second canonical transcript.
Native continuation, tools, reasoning and pending effects stay with their current
owner. This legacy projection does not prove the new multi-participant experience.
That path needs explicit authorship, ordering, duplicate-delivery and membership
behavior; it must not label every agent's output as one assistant or leak private
working context into the shared conversation.

### Process and resource placement

Current Unified already uses per-session native root workers and an idle-retention
owner; the remaining work is not simply to add subprocesses. Reuse
[`RuntimeManager`](../../amplifier_web/runtime.py),
[`WorkerRetention`](../../amplifier_web/runtime_retention.py) and
[`task_environments`](../../amplifier_web/task_environments.py) at their actual
boundaries. Native child execution, broker hydration and host shutdown have
separate costs and lifetimes. [The source update](evidence.md#november-7-ownership-review)
records the inspected implementation and limits.

The first experiment preserves the current root-worker model. Only active work
needs an execution process; reading an idle conversation uses its history owner.
Evaluate conversation-owner isolation and multiple agent instances independently,
without requiring a process per dormant conversation or per repository. Host
restart survival requires a durable supervisor/reattachment decision; keeping a
browser-independent connection is not that guarantee.

Associated DTUs and worktrees name their owner, purpose and consumers. Activity,
pending effects and review holds prevent idle retirement. Use owner operations to
stop/wake or retire resources and reconcile interrupted cleanup. An archive event
can request that reconciliation; it cannot indiscriminately delete shared or
durable work. Installed DTU tooling reviewed for this proposal has no public
suspend/resume operation, so hibernation is a separate tooling change to qualify.

## Ownership and durability

| Data | Authority and durability | When resident / synchronized |
| --- | --- | --- |
| Private drafts, unfinished settings edits, selection, scroll, expansion, layout | Client-local storage, keyed by host/account/conversation/view as appropriate | Only that client; no backend mutation for ordinary edits |
| Intentionally shared draft | Optional AHP chat draft | Explicit feature; standard `chat/draftChanged`, convenient/debounced writes |
| Agent-visible editor context | Explicit targeted capability, bounded snapshot or lease | Requested nonsecret fields only; no permanent mirror of every keystroke |
| Submitted but uncertain input | Client durable intent journal and host admission receipt | Retain exact identity and recovery status; independent of optimistic reducer state |
| Shared messages, participants, conversation metadata and delivery facts | Experience conversation owner; legacy projection preserves current native files | Authorized selected windows and current updates; no broker transcript mirror |
| Native tool history, reasoning, continuation and agent memory | Agent/runtime-selected stores and exclusive native writer | Active execution or explicit authorized retrieval; no second resume authority |
| `events.jsonl` and historical logs | Retained native files on disk | Targeted inspection/export or background indexing when required; no ordinary catalog parse |
| Workspace/session summaries and parent links | Rebuildable disk index over experience/legacy metadata facts | Query pages and incremental invalidations; not a global in-memory model |
| AHP projected chat/session state | Derived view of authorized owner data plus host-owned fields | Subscribed/active resources and bounded cache; explicit upstream snapshot semantics |
| Command receipts, pending decision references and execution routing | Scoped durable admission records; schedules/decisions retain their existing domain owners | Active requests and indexed lookups; survive viewer loss and support restart reconciliation |
| Active model context, modules, hooks, provider connections | Agent execution process | Executing/protected sessions plus deliberately bounded idle reuse |
| Catalogs of providers, modules and capabilities | Disk/package metadata and independently invalidated caches | Requested settings/agent preparation; no mount/test/login during a listing |
| Attachments, tool bodies, artifacts | Stable durable resource references and native ownership | Content on demand; previews/cache bounded by bytes, no inline global replication |
| Audio frames, camera frames, cursor animation, transient progress | Transient stream unless explicitly captured as an artifact | No durable copy by default; accepted transcript/artifact remains durable |
| Credentials | Authorized host/device secret store | Secret-aware path only; configuration status may be shared, raw values may not |

Use asynchronous IndexedDB for browser drafts, outbox and meaningful caches;
reserve synchronous localStorage for tiny preferences. TUI uses its own local disk
store. Distinguish persistent device preferences from independent view drafts so
two tabs do not accidentally share one editor key. Define tab duplication and
restore behavior explicitly. Partition by authenticated host/account, not a raw
session ID that could collide or expose another account's cache.

Clearing a **derived cache** is recoverable from the host. Clearing a **private
unsent draft** is not: the host deliberately never received it. Local restart
survival is the default; cross-device draft continuity is an explicit shared-draft
feature. Quota/storage failures leave a visible unsaved state, not a false promise.
Logout removes sensitive caches under the client policy; private drafts are not
silently uploaded as a workaround.

## Follow AHP's cache and reconciliation model

Use [upstream reconciliation](https://microsoft.github.io/agent-host-protocol/guide/reconciliation.html):
confirmed resource state, pending optimistic actions, and the resulting derived
optimistic state. Keep local presentation state outside that reducer. Route view
subscriptions and render selectors by stable resource identity so an unrelated
chat delta neither rebuilds the app tree nor forces a whole-state serialization.

1. On initial attach, negotiate capabilities, fetch a bounded session page, and
   subscribe to needed root/session/chat resources. Acquire snapshots before actions.
2. During normal use, apply upstream action envelopes, own echoes, rejection and
   rebase rules. Private input/scroll/layout causes no host action. Persist draft
   recovery asynchronously, without putting disk work in each rendering update.
3. On reconnect with a valid base, use `lastSeenServerSeq` and subscriptions. Apply
   replay or fresh snapshots exactly as specified and clear protocol pending actions.
   Re-fetch the session catalog because its ephemeral notifications are not replayed.
4. If local state is missing, corrupt, schema-incompatible or belongs to a different
   authority, subscribe afresh. Never apply deltas to an empty guessed baseline.
   Do not persist AHP catalog cursors across connections.
5. Reconcile the separate durable intent journal with host command/turn identities.
   Do not automatically resend cleared optimistic actions. When a downstream ACP
   agent's acceptance cannot be established after failure, display an unknown
   outcome and retain evidence; do not replay the prompt to discover what happened.

Upstream [draft synchronization](https://microsoft.github.io/agent-host-protocol/specification/chat-channel.html#drafts)
is optional. A new clean editor should initialize from an existing shared draft
when present. A remote shared draft must not silently overwrite dirty private
input. Surface the choice/conflict in the explicit shared-drafting feature.
Private unsaved forms are likewise local. File editors may expose a dirty-buffer
guard to prevent an agent overwriting unsaved work; that safety-relevant fact is
different from publishing every UI field to the backend.

Agent parity for local UI actions uses a targeted client capability: request a
bounded view or ask that client to select, open or edit through its own action
handler. Return the observed result or an explicit offline/unavailable outcome.
This preserves user/agent parity without a permanently synchronized backend copy
of all panels and drafts. Shared work still uses the host's authoritative commands.

## Catalog that fits the actual installation

The historical native-profile fixture targets roughly 4,000 projects and 25,000
native sessions, with fewer existing directories and relevant root conversations.
Other experiences define their own catalog eligibility; a local folder is not a
universal prerequisite. Index small metadata on disk, retaining each fact's owner:

```text
workspace, when present: host, stable identity; native path/existence/freshness
conversation: experience owner/kind, conversation ID, host locator, optional workspace ID,
              parent/root ID, title, modified time, status, storage locator
participation: conversation ID, participant reference, connector locator
execution binding: adapter-managed native identity and writer reference
discovery: writer/source revision, durable change cursor, last reconciliation
query: authorized experience/profile eligibility -> order -> page
```

For the native profile, filter authorization, directory eligibility and root status in the indexed query,
**before** pagination and detail reads. Index parent/root and `(workspace, modified,
stable ID)` for deterministic page order. Do not stat 4,000 paths on every query.
Use tracked directory freshness, watches where useful and throttled revalidation.
Confirmed missing paths leave ordinary results; inaccessible/offline paths become
unknown rather than confirmed missing. Preserve last-known facts for recovery and
show an explicit unavailable source when useful. Archive inspection is separate.

Map an experience conversation to an AHP session with its main chat
and relevant related chats. Workspace grouping is a catalog concern. Do not put
all of a workspace's historical sessions in one giant AHP session chat catalog.
Keep participant/execution bindings separate: an AHP grouping does not imply one
ACP session per chat, or that one ACP session can run all its participants.
This is a conceptual owner map, not new standard wire fields. Qualify the actual
protocol representation and ordinary single-agent fallback before exposing it.
Project-scoped filtering beyond the standard request shape is an optional host
catalog capability, not invented AHP core fields. Standard `listSessions` still
works with a useful bounded default list and upstream pagination semantics.

Children stay indexed and durable but outside ordinary top-level lists. Opening
delegation details requests child summaries/history progressively. Large families
may need a negotiated paged child-discovery extension if the current standard
cannot express the desired bound; the ordinary parent view must remain useful.
Do not silently truncate required standard state to pretend the extension exists.

## Incremental discovery and legacy writers

Make the native adapter publish discovery hints after durable metadata/session
changes, including identity, workspace, parent link, storage locator and revision.
The host indexes hints idempotently. They are not copies of full transcripts and
do not become the authority for resuming execution.

Modify the legacy CLI through shared Foundation persistence hooks where possible:
emit the same small hint or append to a per-writer durable change journal. A killed
writer can miss the post-commit hint, so background reconciliation remains necessary.
An initial import walks lightweight metadata incrementally; historical roots not
yet classified remain an explicit backlog. An old format lacking parent/workspace
facts is enriched once, off the interactive path, with progress and resumable cursors.

Support duplicates, out-of-order hints, watcher overflow, journal rotation and
partial writes. Bounded scans reconcile known roots; schedule periodic discovery
of newly created roots. Never require a full `events.jsonl` parse after each app
action or silently ignore writers that predate the hint mechanism. The CLI bridge
is transitional; the connected TUI becomes the maintained live client.

## Separate runtime and display lifecycles

Persisted does not mean indexed in RAM; indexed does not mean subscribed; subscribed
does not mean an agent is warm. Only explicit execution admission prepares a cold
runtime by default. Reading or selecting a conversation never schedules runtime
preparation. Explicit `session.warm` remains optional and obeys the host’s idle
count, lifetime and background-start budget. The retired `prewarm_on_select`
preference is removed from effective persisted policy; it cannot authorize work.

Use separate budgets for concurrent execution, idle agents, display bytes, native
history windows, replay bytes/age, resource bodies and outbound subscriber queues.
Deduplicate compatible projections, but do not hold an idle worker simply because
a history viewer is open. Pin running work, pending approvals and other genuinely
live effects until their safe boundary; overload queues/refuses new work explicitly.
Retire idle workers with native ownership and durable state intact.

AHP's `view.turns` is advisory. If omitted, upstream requires all retained turns;
`fetchTurns` prepends older turns to reduced chat state. Older-turn operations must
load their referenced history. Our clients should request bounded windows, and
we must test cumulative backfill and other clients that omit the hint. Do not
delete canonical history or secretly change “retained” to evade that requirement.
If large histories need a stronger bound, settle an explicit compatible retention/
archive design or upstream improvement before claiming strict memory bounds there.

## Make update cost proportional to affected work

Replace the global application state as read/write unit with repository methods:
one command receipt, metadata record, catalog invalidation, resource action or
configuration dependency set. Use direct indexed reads for agent queries instead
of building `state_context()` and selecting a JSON pointer afterward. An agent
needing the catalog uses the same filtered pages as a UI.

Scoped snapshots remain valid for initial/recovery reads; incremental actions are
the normal stream. Avoid a hidden global deep-copy/hash/diff merely to compute a
tiny action. Protocol sequencing is a small ordering operation, not a lock held
while files are read, projections built or agent calls awaited. Bound buffering
with standard delivery/reconnect behavior; never drop stateful actions arbitrarily.

Configuration invalidation follows changed source keys and actual dependents.
Persisted, effective and mounted revisions remain separate. One provider setting
may affect many dependent sessions legitimately; it must not mount them all or
rebuild unrelated histories just to report the saved value.

Selected-history Work projections use the disposable process-owned read model in
[selected history projection](../selected-history-projection.md). Its SQLite rows,
sparse checkpoints and spool files are derived caches, never resume or execution
authority. Native sources remain intact; generation fencing and bounded JIT import
apply before publication.

## Long-lived conversations

The same conversation may remain useful for months. Keep three independent sets:
the complete durable record, the current execution/presentation working set, and
the model's selected context. A smaller browser page does not bound the host's
save cost or the model request. A compaction summary is not a replacement for the
original messages, receipts or pending command authority.

The current implementation has bounded `TranscriptWindows` and `WorkWindows` for
the bundled web client, plus an explicitly complete compatibility reader. It does
not yet meet the target throughout the host. Admitted mutable session records can
retain completed messages, worker reports and execution/accounting rows.
`session_projection.persist`, `MessagePreparation` and `ExecutionPreparation`
still inspect historical content during current-owner saves. Caching prepared
bytes reduces encoding but does not make those comparisons independent of history.
The complete `EventIndex` reader also verifies its previous file prefix after an
append; this is not the bounded web-reader path. Do not describe all readers as
incremental merely because they retain an offset.

Implement the remaining separation at the owning write/read boundary:

1. Keep current status, live work and unresolved receipts independently updateable.
   Completed bodies use the existing native history or scoped durable host records
   according to their authority, with exact references and demand-driven pages.
2. Consolidate mutable writers before trusting a change revision. Existing borrowed
   nested aliases can change without a setter; replacing content comparisons with
   object identity, length or an unowned dirty flag would lose changes. Prefer
   explicit owner operations and immutable completed records to another cache layer.
3. Resolve the requested resource before hydration/copying. Whole-history recovery,
   export and legacy complete reads remain explicit, with their cost and coverage
   visible. Preserve historical editing, attribution, accounting and pending effects;
   an arbitrary last-N slice cannot replace their authority.
4. Keep model-context selection with the installed context owner. Qualify compaction,
   durable summaries and on-demand retrieval across resume, including instructions,
   pending tool pairs and task continuity. Do not send raw event logs as context or
   silently discard older user requirements to make a request faster.

This is an implementation sequence, not a claim of completed migration. Validate
each owner independently with a fixed current workload and growing completed history;
include cold/restart and explicit older-detail reads. The
[diagnostic baseline](../validation/performance.md#long-lived-conversation-baseline)
records current save costs without inventing a universal latency threshold. This
work applies to the existing application and does not depend on AHP/ACP adoption.
