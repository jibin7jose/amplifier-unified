# Shared client state — v1 (RATIFIED 2026-10-02)

Clause prefix: **CS**. Parent: [shared-client vision](../docs/clients/VISION.md).
This specifies proposed ownership and synchronization behavior, not a new wire format.

## Who builds against this

Unified service and runtime owners, Foundation maintainers, and all connected client builders.

## What it is

```text
Conversation identity -> experience owner -> shared transcript and participants
Participant execution binding -> exclusive native writer -> native continuation storage
Client A -> conversation X + private presentation + explicit subscriptions
Client B -> conversation X or Y + its own presentation + explicit subscriptions
Configuration: persisted scope -> effective merge -> mounted runtime revision
Command: pending -> rejected / accepted / unknown; accepted -> execution outcome
```

[The scope map](../docs/clients/state-scopes.md) assigns data to these boundaries.
[Wire v1](../docs/clients/live-sessions.md) defines current operations; the target
[HP contract](host-protocol.v1.md) delegates replication to upstream AHP.

## The promises

1. **CS1 — Identify the host and the target.** Commands capture the intended host and
   conversation and, where relevant, participant/execution target before waiting.
   Multiple clients use the same conversation and execution owners; detaching a view
   does not stop accepted work. Client identity is not authentication.
   Broken: switching selection retargets pending input or a second viewer creates a second runtime. Affected: concurrent users.

2. **CS2 — Separate shared state from each view.** Accepted work, questions and durable
   conversation properties are shared; ordinary selection, drafts, unsaved edits and
   reading state persist on the client with no backend sync. Shared drafts or targeted
   nonsecret agent inspection are explicit capabilities using upstream semantics.
   For the current web adapter, ordinary agent overviews, generic state pointers,
   dispatch readback and browser observations exclude unsent composer text,
   message edits and attachment drafts. User-requested inspection uses the exact
   `/view/draft` pointer plus an explicit `clientId` displaying the calling conversation;
   the exact `/view/messageEdit` pointer likewise inspects an unfinished message
   edit belonging to that chat. Selecting a client on a broader read does not opt in.
   Private UI persistence remains independent of this model projection; its client-local migration is
   tracked separately.
   Broken: another window overwrites unfinished input or a secret becomes public view state. Affected: people and agents sharing a host.

3. **CS3 — Publish by interest and dependency.** Detailed conversation updates go only
   to its subscribers; summaries and configuration changes reach their own interested
   subscribers. No viewers requires no detailed view construction. Slow clients cannot stall execution or grow unbounded queues.
   Broken: activity in X rebuilds Y's unrelated detail or every settings panel. Affected: all users as client count grows.

4. **CS4 — Reconcile without repeating work.** Retain command identity and payload
   before sending; distinguish rejected, unknown, accepted and completed. Exact retries
   follow negotiated receipt rules and original identity. AHP recovery follows HP3;
   the durable intent journal survives clearing optimistic pending actions, without automatic command or device-effect replay.
   Broken: a missing reply means failure, a snapshot duplicates messages, or reconnect repeats a tool. Affected: users recovering connectivity.

5. **CS5 — Name configuration scope and provenance.** A setting identifies its host/root,
   global/project/workspace-local/session scope and effective source. Shared scope does
   not imply cross-host replication. A stale edit conflicts or merges explicitly without erasing unrelated updates or local drafts.
   Broken: a workspace override is silently lost or a Mac save is presented as a Spark save. Affected: settings editors and dependent sessions.

6. **CS6 — Separate saved, effective and mounted.** Saving configuration reports where
   it persisted and when it can apply. Affected sessions expose the applied revision or
   pending/restart requirement; unrelated sessions and explicitly overridden fields remain unaffected. Refreshing a catalog is not editing configuration or mounting a provider.
   Broken: saved defaults are labelled active in an unchanged worker. Affected: operators choosing runtime behavior.

7. **CS7 — Preserve canonical storage and exclusive execution.** The experience owns
   shared conversation history under [HP7](host-protocol.v1.md). Native adapters own
   execution history and coordinate its writers using their mechanisms (Foundation
   for native Amplifier). The current single-agent adapter may expose its native
   history as the conversation view; that compatibility mapping does not merge the
   general identities or require a second transcript store. Connected clients mutate
   through the host; independent apps acquire ownership before writable native loading.
   Reacquisition reloads current data; release does not prove effects stopped.
   Broken: two owners write, a stale worker overwrites history, or a second store controls the name. Affected: all shared-session participants.

8. **CS8 — Target device effects explicitly.** Microphone, camera, clipboard, local files,
   notification permission and similar effects target a capable authorized client/device.
   Their delivery is separate from shared conversation updates and has an explicit unavailable/unknown result.
   Broken: every viewer opens a microphone or reconnect repeats a clipboard write. Affected: people with several devices attached.

## Not in v1

Execution migration, shared filesystem locking across independent machines, automatic
credential replication, event-log-only recovery, pruning, or exactly-once remote tool effects.
Existing wire-v1 snapshots remain supported during migration; target AHP replication follows HP.

## How the kit checks it

Exercise [the acceptance journeys](../docs/clients/development-plan.md) with two conversations,
multiple clients, overridden configuration, a slow subscriber, disconnects and an owner handoff.
Measure publication work as well as bytes; ratification alone establishes no conformance verdict.

## Open questions

Which legacy clients need the temporary facade and for how long?
Which advanced controls need negotiated extensions beyond standard AHP/ACP?

## Changelog

| Date | Change | Basis |
| --- | --- | --- |
| 2026-11-07 | Distinguish shared conversation ownership and participant targets from exclusive native execution; preserve the single-agent compatibility mapping. | [Conversation ownership](../docs/architecture/state-and-storage.md#conversation-participation-and-execution); multi-participant implementation remains unqualified. |
| 2026-10-02 | Ratified for implementation; exclude amplifier-agent as the native backend. | User approval of the plan and direction; implementation evidence remains separate. |
| 2026-10-02 | Client-local persistence, AHP recovery and generic native ownership. | [Protocol architecture](../docs/architecture/README.md); user-approved direction, ratified below. |
| 2026-09-20 | Initial draft, CS1–8. | Shared-state discussion and source/owner boundary review. |
