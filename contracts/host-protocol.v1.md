# Host protocol — v1 (RATIFIED 2026-10-02)

Clause prefix: **HP**. Parent: [family vision](../docs/architecture/VISION.md).
Governs the generic host and its AHP consumers; upstream owns the wire semantics.

## Who builds against this

Host implementers, web/TUI/independent client builders, capability authors and reviewers.

## What it is

A durable host represents authorized agent work through AHP. Its catalog and
resource subscriptions do not require consumers to import the host implementation.

```text
client -- AHP initialize / listSessions / subscribe / actions --> host
host -- negotiated snapshots, action envelopes, notifications --> client
host -- ACP --> participating agent's execution adapter
```

The [protocol design](../docs/architecture/protocol-fit.md) cites upstream authority;
[CS](client-state.v1.md) owns client intent, configuration and device effects.

## The promises

1. **HP1 — Adopt the upstream protocol.** Negotiate supported AHP versions and use
   their schemas, actions and reducers. Upstream rules take priority over local
   design notes; optional namespaced extensions never redefine a standard action.
   Broken: a stock peer needs a private reducer to read ordinary chat. Affected: all clients.

2. **HP2 — Separate resources and catalogs.** Publish AHP state by resource and
   interest. List sessions through the standard paged catalog; load conversation
   details only when required. [WS](working-set.v1.md) owns filtering and work bounds.
   Broken: a root subscription contains every saved transcript. Affected: large installations.

3. **HP3 — Recover through upstream semantics.** Sequence, echo, rejection, replay
   and snapshot recovery follow the negotiated AHP version. A missing confirmed
   baseline requires fresh state; notification-only catalogs are refetched as specified.
   Broken: a delta is applied to an absent base or reconnect repeats intent. Affected: reconnecting clients.

4. **HP4 — Preserve independent lifetimes.** Host routing, agent connections and
   admission receipts survive viewer connections; the execution owner retains
   accepted work. Multiple authorized views use those same owners. Resource
   subscriptions alone never grant execution or filesystem permission.
   Broken: closing a tab cancels work or bypasses approval. Affected: unattended work and other viewers.

5. **HP5 — Make capability limits visible.** Map agent capabilities to honest host
   operations. Unsupported queue, steer, takeover or device behavior is unavailable,
   never silently translated into a different action. [AP](agent-protocol.v1.md) owns agent mappings.
   Broken: a baseline cancel claims live steering or force termination. Affected: operators.

6. **HP6 — Keep admission independently recoverable.** Persist host command identity
   and admission outcome before dispatching accepted execution. Reconcile unknown
   outcomes without automatic resubmission. AHP echo is not proof of completed effects.
   Disposable body expiry preserves request identity, fingerprint, scoped owner and
   truthful admission/outcome, including uncertainty and detail availability. An
   exact legacy retry still returns its saved outcome or explicit expired detail;
   conflicting contents still reject. Expiry never regenerates content by replaying
   an effect and never turns a receipt into a permission grant. Current authority
   and unresolved recovery bindings cannot expire merely through age. Legacy
   arbitrary request IDs keep compact no-replay identities indefinitely; a future
   retry horizon requires a separately versioned, negotiated protocol and durable
   closed-epoch boundary before any identity can be forgotten.
   Broken: retry after a lost response starts another turn. Affected: people with unstable connections.

7. **HP7 — Route to the conversation owner.** The experience owns its shared transcript,
   participant membership and delivery facts. The broker routes authorized reads,
   inputs and updates without becoming a second transcript store. Agent participants
   can retrieve their connected conversations through adapters; native model context
   is not the shared transcript. Workspace grouping adds shared files/context, not
   another mandatory conversation or workflow authority.
   Broken: connecting an agent transfers authority over shared history to its private runtime,
   or a catalog read hydrates every conversation. Affected: participants and host operators.

## Not in v1

Cross-host execution migration or cross-engine continuation; promote only with separate
ownership and recovery contracts. No private replacement for AHP's reconciliation rules.

## How the kit checks it

- HP1/HP5: run standard peers and schema fixtures, including absent extension support.
- HP2: trace catalog/detail reads using WS's large-history fixture.
- HP3: drop/reorder connections, expire replay and remove the client baseline.
- HP4: disconnect every viewer during work; deny an unauthorized operation.
- HP6: kill around admission/dispatch and lose replies; count accepted turns and effects.
  Repeat exact/conflicting requests after body expiry, restart and backup recovery;
  preserve unknown outcomes and verify no additional execution.
- HP7: two agents and a person share one transcript; disconnect/reconnect an agent,
  retrieve an older page, and change membership without duplicating or reassigning history.

Implementation is not yet verified. [The plan](../docs/architecture/delivery-plan.md) records delivery gates.

## Open questions

Which supported AHP SDK/runtime combination best serves the extracted host?
Which command receipts can be recovered with upstream identities alone before any extension?

## Changelog

| Date | Change | Evidence |
| --- | --- | --- |
| 2026-11-07 | Separate conversation ownership from broker routing and native execution; add HP7. | [Ownership design](../docs/architecture/state-and-storage.md#conversation-participation-and-execution); no current multi-participant conformance claim. |
| 2026-11-07 | Preserve admission and legacy exact retries independently of disposable detail expiry. | User-approved finite-lifetime design; [Storage policy](../docs/STORAGE.md#approved-prospective-retention-policy). |
| 2026-10-02 | Ratified for implementation; exclude amplifier-agent as the native backend. | User approval of the plan and direction; implementation evidence remains separate. |
| 2026-10-02 | Initial HP1–6. | [Protocol review](../docs/architecture/protocol-fit.md). |
