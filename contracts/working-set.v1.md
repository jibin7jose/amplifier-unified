# Catalog and working set — v1 (RATIFIED 2026-10-02)

Clause prefix: **WS**. Parent: [family vision](../docs/architecture/VISION.md).
Governs discovery, residency and durable-history boundaries.

## Who builds against this

Catalog, host, runtime, native-store and client maintainers; operators with large histories.

## What it is

Historical existence, visible catalog membership and runtime residency are separate facts.
Durable history supports on-demand access without loading all stored work into memory.

```text
durable native records -> rebuildable disk index -> filtered catalog page
selected conversation -> requested history window + requested child summaries
accepted execution -> runtime admission -> working -> safely evictable when idle
```

[State and storage](../docs/architecture/state-and-storage.md) provides the design.

## The promises

1. **WS1 — Preserve the durable record.** Experience-owned conversations and native
   execution records retain their respective owners across catalog exclusion,
   index rebuild and cache eviction. Native transcripts, metadata and retained
   event logs remain intact. Derived
   indexes never become an alternative resume authority or replay past execution.
   Broken: removing a missing directory deletes its history. Affected: owners of historical work.

2. **WS2 — Filter before loading pages.** Apply authorization and the experience's
   catalog eligibility before pagination. In the native filesystem-backed profile,
   default project discovery includes confirmed existing workspace directories and
   their root sessions. Unknown or unreachable paths have an explicit state, not an
   inferred deletion. Other experiences do not require a local folder to list conversations.
   Broken: paging loads all 25,000 sessions and filters in the browser. Affected: ordinary browsing.

3. **WS3 — Load detail by demand.** History windows, child conversations, large tool
   bodies and artifacts are fetched for the selected resource. Reading history does
   not initialize an agent. AHP retention and window semantics remain authoritative.
   Routine current-state projections preserve fields their consumers need without
   repeating unused historical bodies. Omitted detail retains its authorized read
   path until an explicit WS7 retention policy expires it; projection alone never
   truncates the canonical record or invents empty coverage.
   Broken: opening the sidebar starts dormant workers. Affected: people and active executions.

4. **WS4 — Bound resident work.** Runtime, projection, replay and subscriber caches
   have explicit count/byte/idle or admission budgets. Active execution and unresolved
   decisions cannot be evicted as idle; overload has a visible queued/refused outcome.
   Protecting an active conversation does not require keeping all its completed
   message, worker and tool bodies resident. Current execution and pending decisions
   retain exact identities; completed detail remains accessible through durable reads.
   Associated processes and temporary environments can retire or wake through their
   lifecycle owner only when actual work, other consumers and review holds allow it.
   Viewer absence alone is not an idle witness; archive is not permission to delete
   durable conversation or agent memory. Retention follows its separate owner/policy.
   Broken: one slow client grows memory without limit or eviction cancels work. Affected: all users.

5. **WS5 — Charge updates to dependencies.** A resource update touches its record,
   actual dependents and subscribers. No request performs historical global scan,
   projection, hash or checkpoint to process a draft, scalar read or unrelated chat delta.
   Review includes work before response filtering and serialization, including
   conversion performed before an encoding-cache hit. Reusing unchanged execution
   must avoid repeating its projection; changed content, failed commits and cold
   services must still preserve exact receipts and pending-work facts. A required
   full-history operation, such as recovery validation, declares its trigger and
   scaling inputs; it must not become repeated background work for unrelated updates.
   This also applies within one long-lived conversation: a current token or scalar
   change must not require traversing, comparing or encoding its entire completed
   history. Cache-hit validation counts as work, even when nothing is rewritten.
   Runtime diagnostic observations use their diagnostic owner; they do not dirty
   conversation history merely to make an inspection view current. Preparation
   reuses a request-scoped observation within one fitted request, without reusing
   it across requests or replacing transport-time authority/revocation checks.
   Full catalog metadata reconciliation commits its changed owners and global
   records without checkpointing unchanged conversation histories. Pending progress
   still joins that commit, and failed commits remain recoverable.
   Text-only history retrieval does not reconstruct execution activity that its
   consumer discards. Requested activity detail remains available through its
   explicit display read; omitted detail is not reported as empty coverage.
   Broken: a small update scales with unrelated stored histories. Affected: multi-session hosts.

6. **WS6 — Discover without rereading history.** New native writers publish incremental
   discovery hints after durable changes. Bounded reconciliation recovers missed hints;
   initial migration can scan in the background with visible progress and throttling.
   Broken: every refresh parses every events file or missing a watcher loses a session forever. Affected: legacy users.

7. **WS7 — Give disposable payloads an explicit lifetime.** A standing Storage
   policy may expire explicitly disposable completed bodies at their canonical
   owner, independently of the small admission/outcome receipt. First activation
   applies prospectively; legacy records need an explicit scoped disposition.
   Preserve current policy/grants, active or unknown effects, native history and
   durable deliverables. Retained consumers, review pins and recovery needs protect
   their required bodies. Reads and metadata inspection never restart a lifetime.
   Expired detail remains distinguishable from missing work and from an empty
   result; inspection never reexecutes or fetches an effect to reconstruct it.
   Disabling policy stops new expiry admissions, and size pressure never shortens
   a promised lifetime. Existing explicitly documented Smart Tool result budgets
   retain their count/byte limits. Bounded owner passes recheck references and
   policy at commit/removal; uncertain removal remains recoverable.
   [Storage policy](../docs/STORAGE.md#approved-prospective-retention-policy) names
   approved defaults and separates implemented behavior from subsequent batches.
   Broken: an old receipt looks like a new request or unlink deletes a deliverable.
   Affected: unattended work, recovery and retained results.

### Nested tool checkpoint boundary

A verified active `tools.dispatch` child does not append a conversation result.
Its automatic completion boundary persists control changes and validates the
current native writer without copying, encoding or checkpointing the full
transcript. Existing enclosing tool, explicit checkpoint, compaction and terminal
error/cancellation boundaries still save context changes. The loop retains
ownership of ordered parent-result append; this optimization adds no new durable
append callback or stronger crash-recovery guarantee. Unknown or expired dispatch
attribution uses the existing full checkpoint. Observed effects are never inferred
to have rolled back or replayed merely because the parent is interrupted.
Production Work-wrapped children under Full access with incomplete assurance share
the enclosing transcript checkpoint without changing their assurance eligibility.

## Not in v1

Automatic native-history deletion and archival compression remain separate retention
decisions. WS7 applies only to explicitly disposable kinds under the approved Storage
policy. Cross-machine path equivalence is not inferred from a matching path string.

## How the kit checks it

- WS1: checksum native records before/after exclusion, index loss and recovery.
- WS2/WS3: trace filesystem reads and worker starts on the large-history fixture.
- WS4: disconnect viewers, stress slow subscribers and exhaust runtime admission.
- WS5: compare update work as stored sessions grow with the active set fixed, and
  as one active conversation's completed history grows with its current work fixed.
- WS6: duplicate/drop hints, interrupt rebuild and restart the legacy writer.
- WS7: expire settled bodies without replay; preserve active/unknown and referenced
  content, race policy disable/promotion with expiry, and distinguish expired detail.

Use [performance validation](../docs/validation/performance.md) to declare workload
shape, operation counts and candidate budgets alongside semantic regression checks.
Numeric results belong to the exact fixture and evidence record, not as passes here.

## Open questions

What residency defaults meet measured latency and memory budgets on supported hosts?
Which directory freshness policy handles offline mounts without noisy catalog churn?

## Changelog

| Date | Change | Evidence |
| --- | --- | --- |
| 2026-11-07 | Distinguish conversation/native records, scope filesystem eligibility to its catalog profile, and tie idle release to owners/consumers. | [Lifecycle design](../docs/architecture/state-and-storage.md#conversation-participation-and-execution); no DTU hibernation implementation claimed. |
| 2026-11-07 | Ratify prospective owner-specific disposable payload lifetimes; preserve current authority, durable/native content and exact retry identities. | User-approved finite-lifetime design; [Storage policy](../docs/STORAGE.md#approved-prospective-retention-policy). |
| 2026-10-30 | Clarify same-conversation history scaling and distinguish protected execution from completed-body residency. | [Long-lived conversations](../docs/architecture/state-and-storage.md#long-lived-conversations); diagnostic fixture in [performance validation](../docs/validation/performance.md#long-lived-conversation-baseline). |
| 2026-10-30 | Clarify diagnostic ownership and request-scoped preparation under WS5. | Focused runtime progress and preparation checks in [performance validation](../docs/validation/performance.md). |
| 2026-10-29 | Clarify current-state/history projection and accountable full-history work in WS3/WS5. | [Performance validation](../docs/validation/performance.md); user-requested guardrails. |
| 2026-10-02 | Ratified for implementation; exclude amplifier-agent as the native backend. | User approval of the plan and direction; implementation evidence remains separate. |
| 2026-10-02 | Initial WS1–6. | [Scale requirements and measurements](../docs/architecture/evidence.md). |
