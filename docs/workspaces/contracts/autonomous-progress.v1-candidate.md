# Autonomous progress — proposed continuation contract

**DRAFT for review, 2026-11-07. Not implemented or release-qualified.**

## Who relies on this

People and managers should not have to connect routine settings, waiting and
resumption steps for work that is already authorized. The configuration, native
execution and task-continuation owners implement this boundary; clients explain
its state. [Configuration authority](../configuration-authority.md) and
[execution](execution.v1.md) retain ownership of permissions, runtime identity
and execution safety. This proposal does not grant new authority.

## The behavior

When an active task requests an authorized change to its own runtime settings,
the host accepts the change, reaches the required execution boundary, applies and
verifies the settings, and continues the same objective. The task does not need a
separate block, resume command or message to itself.

```text
Working → Waiting for settings — will continue automatically → Working
                         └→ Recovering → Working / specific blocker
```

The first scope is an active task changing its own settings. Changing settings on
an idle, completed or deliberately paused task does not start work. Cross-chat
changes, application updates and device input retain their existing owners.

## Promises

### Waiting preserves the objective

Accepting a change that requires yielding durably binds continuation intent to
that change and the current task before execution yields. The objective, limits
and authority remain intact. A change that can apply immediately needs no
artificial wait. Leaving authorized work stranded until somebody sends a nudge
is a failure of this boundary.

### Yield settles the actual execution

The existing native execution owner settles admitted tools and children before
replacing their runtime. Completed effects and input identities remain recorded.
An idle root does not establish that its children have settled, and saved settings
do not establish mounted readiness. An unresolved boundary identifies the actual
owner or effect that prevents it from settling.

### A settled result continues the same work

Verified readiness makes the same task eligible through existing admission.
There is at most one logical continuation for the operation and task identity;
restart or duplicate completion delivery does not create another initial turn.
Eligible fresh input or an existing continuation may consume that readiness.
Other prerequisites still apply; a capacity wait remains queued for automatic
admission. This does not promise exactly-once external effects or authorize replay
of old inputs, tool calls or writes.

### Current intent governs continuation

New user input takes precedence without being lost or delivered twice. Stop,
deliberate pause, an objective or ownership change, and revoked or expired
authority suppress obsolete continuation. Routine progress edits do not. Checking
current authority does not mean requesting it again. Previously unsent input
remains preserved; editing an inactive chat's settings creates no objective.

### Recovery belongs to the operation owner

The owner uses the operation's receipt and readback to finish known partial work
or settle a known no-effect failure. An unknown outcome requires inspection,
not guessing or replay. Ordinary recovery within existing authority needs no
manager relay or model-driven polling. If readiness cannot be established, the
result names the missing fact, responsible owner and next action. An indefinite
generic settings fence is not an acceptable terminal result.

### Waiting costs only relevant work

Recovery and restart inspect enrolled operations with bounded work. They do not
scan every conversation, repeatedly load full histories or spend model turns
polling. Observation creates no objective and repeats no uncertain effect.
Workers handle ordinary repair within their assigned scope; managers choose
priorities and accept deliverables.

### The visible state has one meaning

Receipts and UI distinguish automatic waiting, recovery, a specific blocker and
resumed work. Accepted, saved, mounted and completed are different observations.
The UI does not demand an extra resume step after the host can continue. A
historical unsent message is not evidence of the current task's state. Guidance
must describe only qualified capability, and retire the corresponding manual
workaround when automatic continuation ships.

## How this will be checked

The [focused acceptance plan](../notes/autonomous-progress-20261107.md#focused-acceptance)
covers the installed host, actual worker and built UI, including restart,
duplicate delivery, children, changed intent and unknown outcomes. These are
planned checks, not passed evidence.

Targets are zero routine human nudges, zero model polling turns, and one consumed
logical continuation per eligible result. Measure admission delay separately from
runtime preparation and model latency.

## Deliberately outside this first contract

- Cross-chat configuration needs its own authority and continuation review before
  extending this boundary.
- Device-control leases and input freshness remain with the shared computer-use
  owner; standing permission does not itself establish current device readiness.
- A new scheduler, database, broker, supervisor or history migration is not an
  assumed prerequisite. Reuse the existing execution and operation owners.
- Legacy blocked work is reconciled individually. A free-text historical promise
  to continue is neither a durable continuation record nor new authorization.

## Change record

- 2026-11-07: proposed own-task settings continuation; separated observable
  promises from diagnosis, implementation choices and planned qualification.
  Earlier draft clauses map as follows: 1 → **Waiting preserves the objective**;
  2 → **Yield settles the actual execution**; 3 → **A settled result continues
  the same work** and **Current intent governs continuation**; 4 → **Recovery
  belongs to the operation owner**. Clause 5, **Permission is not a timer**, moves
  to the [separate device follow-up](../notes/autonomous-progress-20261107.md#separate-device-freshness-follow-up),
  preserving deliberately time-limited grants and the shared owner's policy.
