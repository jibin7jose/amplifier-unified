# Settings continuation: design and acceptance

**Design accepted in #1640, 2026-11-07. The first implementation is under review;
this document does not establish publication or live adoption.**

The [contract](../contracts/autonomous-progress.v1-candidate.md) owns
the observable promises. This note explains the diagnosis, implementation
choices and delivery sequence; it does not introduce additional binding promises.

## Why start at this boundary

The review baseline is Unified `efa6c0762a03cc8884e316fc4652e811a0449ed5`
(0.60.0). In the retained incident, settings reached ready/readback/mounted state
while the task remained blocked. Resuming the task and admitting fresh input were
separate operations; the older unsent input remained untouched. Neither a settings
success message nor a changed task status guarantees a working continuation.

| Existing friction | Proposed responsibility |
| --- | --- |
| Queued configuration waits for root and child idleness while the goal can keep running. | Save the continuation intent and request a real native yield boundary. |
| Configuration completion can publish a message without making the task continue. | Enroll the waiting task before yielding and consume the verified result through admission. |
| Changing a task from blocked to active does not itself admit input. | Represent this as a machine-owned wait for the active objective. |
| Recovery facts are spread across owners and relayed through managers. | Return a bounded disposition from the original operation owner. |

Keep intent, authority and readiness separate. An accepted objective and standing
permission can remain valid while its runtime changes. Stop, deliberate pause,
revocation, an uncertain effect or a real ownership conflict still matters. None
of those requires asking again for routine repair that is already authorized.

## One operation, using existing owners

Bind the accepted configuration operation to the current task identity, execution
boundary, applicable Stop/authority state and eventual outcome. Determine which
existing records already carry those fields before adding any. This is not a
proposal for another journal or a second source of task truth.

Persist that binding before requesting yield. The native boundary must settle
admitted tools and children, including persistent children that are idle but still
mounted. Preserve task budgets, input identities and completed effects. Applying
the saved configuration is followed by the existing readiness/readback check;
only that verified result makes a replacement runtime eligible.

Use a stable logical continuation identity to coalesce duplicate delivery and
new input through existing admission. Recheck all current prerequisites there.
If two operations must settle, completing the first cannot run ahead of the
second. If work has already continued, another completion notification does not
start an extra turn. Restart recovers the enrolled wait and its result; it does
not repeat the original configuration command or tool work.

| Owner in the current source | Responsibility to keep or extend |
| --- | --- |
| `management.py`, `configuration_recovery.py` | Configuration acceptance, queued apply and authoritative outcome/readback. |
| `handoff_boundary.py`, `task_continuity.py` | Native yield, exact execution settlement and continuation intent. |
| `task_binding.py`, `collaboration.py` | Task identity and admission; suppress obsolete or already-consumed continuation. |
| `update_drain.py` | Reference the existing `task.waitForUpdate` path for shared wait mechanics, retaining update-specific verification. |
| Client and coordination guidance | Explain automatic waiting; remove the corresponding manual resume choreography after qualification. |

These are investigation starting points, not a new ownership layer. The first
slice should need no Core protocol change, broker, database, model-powered
supervisor, permission hierarchy or scratch-file workflow. If a necessary native
boundary is absent, identify that concrete dependency instead of inventing a
parallel checkpoint mechanism. Restart and recovery work must scale with enrolled
operations, not the installation's entire conversation history.

## Recovery with a concrete disposition

| Observed outcome | Owner behavior |
| --- | --- |
| Confirmed no effect | Release only the obsolete operation fence and return the actual failure; do not retry an unchanged failing command indefinitely. |
| Saved, not yet mounted | Finish the applicable readiness/readback path without repeating the saved write. |
| Unknown outcome | Inspect the exact retained backend/operation identity; only a verified result can permit continuation or safe recovery. |
| Unresolvable outcome or genuine authority/ownership conflict | Preserve evidence and return the specific blocker, responsible owner and available next action. |

Prefer deterministic host recovery to asking a model to repair the runtime it
needs to run. Retry only conditions with a defined recovery path; avoid unchanged
failure storms. Capacity admission remains with its current owner. A proven
terminal outcome must not leave an ownerless settings fence that blocks all new
input forever.

## Focused acceptance

The table defines acceptance scope, not blanket passed evidence. Use maintained tests and an owned DTU with an
installed host, actual native worker and built client. Source-level tests alone
cannot establish the continuation behavior.

| Case | Required observation | Contract promise |
| --- | --- | --- |
| Active goal changes its own settings | Yield, verified application and continuation of the same objective without an extra orchestration turn. | Waiting preserves the objective; A settled result continues the same work |
| Active or persistently mounted child | Exact root/child settlement before replacement; no interrupted completed effect is replayed. | Yield settles the actual execution |
| Crash or lost acknowledgement before yield, after save, or after admission | Saved intent survives; no duplicate write, input, tool effect or reset budget. | Waiting preserves the objective; A settled result continues the same work |
| No-effect failure, saved-but-unmounted change and unknown outcome | Each follows its defined disposition; unknown effects remain inspectable and are not replayed. | Recovery belongs to the operation owner |
| Stop, pause, revocation, expiry or changed objective/owner | Suppress stale continuation; routine progress edits do not; later explicit resumption uses current intent. | Current intent governs continuation |
| Fresh input races completion; inactive chat settings change | Preserve unsent input and admit fresh input once; create no objective for the inactive chat. | Current intent governs continuation |
| Duplicate results, two pending prerequisites and temporary capacity wait | Admit one eligible continuation only after all prerequisites; no model polling. | A settled result continues the same work |
| Many unrelated sessions and large histories | Read only relevant enrolled records; unrelated history growth does not multiply recovery work. | Waiting costs only relevant work |
| Built UI across acceptance, save, readback, recovery and continuation | Show truthful distinct states and the precise blocker when needed; no unnecessary resume prompt. | The visible state has one meaning |

Measure the contract's zero-nudge/zero-model-polling targets. Aim to enqueue an
eligible continuation within one second of verified readiness on a warm idle
host; this is a design target to measure, not an already established guarantee.
Separate enqueue delay, capacity wait, runtime preparation and model time. Record
source/runtime identities and cold/warm conditions. Use the existing
[release policy](../../validation/release-qualification.md); this proposal adds
affected checks, not a broad per-release campaign.

## Delivery and retirement

The first implementation stores continuation intent in the existing private
configuration operation. `configuration_continuation.py` connects that owner to
the existing native handoff boundary and command-ledger continuation. A native
control checkpoint permits recovery of a lost boundary reply only after the
same writer is proven stopped. The replacement reads the saved goal and task;
it does not create a new goal or reset counters. No new queue, scheduler, storage
authority or Core change is introduced.

Maintained checks cover the host transition, changed intent, cancellation,
no-effect failure and lost checkpoint in `test_configuration_continuation.py`;
the actual Core/live loop in `test_configuration_continuation_native.py`; and
the actual process restart through `test_queued_configuration_boundary.py` and
its native probe. The native probe uses a deterministic provider, verifies two
distinct worker processes, one settings write and one continued input, and
preserves goal usage. It is not paid-provider or live-workload evidence.
`configuration-recovery.test.mjs` checks the visible waiting explanation without
issuing another command. Existing child-drain, handoff, update and configuration
recovery checks remain separate owners of their broader boundaries.

Release evidence must record the tested source and installed build, including
any cases still unqualified. The whole acceptance table must not be marked
passed from these focused checks alone.

1. Implement and qualify the own-task settings boundary first. Ship the behavior
   and retirement of its manual guidance together.
2. Reconcile legacy blocked work individually from current operation/task facts.
   Where no durable continuation exists, use the supported current continuation
   path; do not infer one from historical prose or bulk-resume inactive chats.
3. Review other demonstrated wait failures for the same missing transition.
   Reuse shared mechanics where appropriate while keeping domain-specific outcome
   verification with its owner.
4. Observe actual deliverables, human interventions, wait time and duplicate
   work. Extra status messages or a green synthetic check are not productivity.

## Separate device-freshness follow-up

Standing permission and a current device lease are different facts. A deliberately
time-limited user grant ends at its deadline and is not renewed implicitly. A host
freshness lease can expire while a long task remains authorized. The shared
computer-use owner should evaluate renewing it from current target identity,
exclusive ownership and presence observations when the standing policy permits.
Actual takeover must still stop input; ambiguous presence needs an explicit
owner policy. Increasing every timeout or bypassing device checks in Unified
would hide that boundary instead of fixing it.

That shared proposal, cross-chat continuation and any broader storage redesign
are separate from the first settings slice. They are not new prerequisites for
unrelated releases.
