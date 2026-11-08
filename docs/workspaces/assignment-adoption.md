# Adding an existing chat to an assignment

A manager can explicitly take responsibility for a settled, independent chat
in the same workspace. Sharing a folder never grants that responsibility.
This extends assignment membership without changing the conversation's native
identity, history, working folder, previous usage, or commissioning provenance.

`assignment.adoption` reviews one target and returns an opaque revision bound
to the assignment, target input, Stop, generation, task and execution placement.
`assignment.adopt` checks that revision while saving both the membership and
the command receipt. The UI offers **Add an existing chat** inside Shared tasks
and assignment; its picker reads twenty workspace chats per page. Inspection
does not start a worker.

An agent must be the assignment owner acting from its current actual human
input, identify that message, and honor the user's requested scope. Peer input,
scheduled input, and an old human message do not supply this authority. A UI
selection is direct user authorization. Existing assignment ownership, active
work, pending input, and settings changes prevent enrollment. The existing
member and open-task ceilings apply.

Membership has role `adopted` and its own adoption receipt. It does not invent a
parent or creation receipt. The manager may subsequently use the existing
guarded peer-resume and idle-checkout placement paths. Adoption itself sends no
input and does not clear Stop, a saved task pause, a failure or a budget.
Placement verifies the exact adoption receipt, with the same asynchronous
instruction/Stop/membership checks as commissioned placement.

Future calls use the assignment budget. Earlier calls keep their existing
attribution; detachment retains charged usage and revokes manager control.
Detached historical membership is not recycled into a new authority epoch:
use a different assignment to establish a new relationship.

## Finishing and reusing a member chat

The assignment owner can use `assignment.retire` for a finished helper. This
releases its current member slot without archiving the chat, deleting files,
forgetting its membership or subtracting any attributed usage. Running workers,
unsettled inputs or checks, unfinished saved tasks and shared task claims prevent
retirement. A native ownership check protects the transition. User Stop and
unknown operation outcomes are not cleared.

Saved-task eligibility and the reuse binding read the canonical native control
record under that ownership check; a lost reply or cached host projection cannot
prove completion. Missing or malformed saved state stays unknown, while an
available `task: null` proves no saved task; ordinary human/UI adoption remains
unchanged.

`maxMembers` counts current bound members and reservations. Retired, detached
and never-created records remain available as history without occupying those
slots. The brief and assignment controls distinguish current and retained
membership counts.

For a later follow-up, the existing `assignment.adoption` / `assignment.adopt`
path can add a retired chat to the same assignment again after a fresh review.
It rechecks capacity and native ownership, records a new membership boundary,
and retains the previous retirement receipt. Inputs and automatic returns from
before readmission remain historical; they cannot restart the chat. Prior
spending continues to count, and adoption itself sends no input.

Under standing **Full access**, the current assignment-owning root can explicitly
readmit its own finished retired member without a fresh human input. This implements
the [standing authorization direction](configuration-authority.md#unattended-full-access-direction):
requiring a human relay for an unchanged, already-owned follow-up wastes attention
without establishing new authority. The exception is not independent-chat takeover.
The host verifies the exact same-owner retirement receipt and its saved reuse
binding: native identity, placement, configuration/access, Stop, target generation,
completed task and manager task scope must remain unchanged. New retirement
receipts retain that binding; older receipts without it do not establish this
exception. The calling native principal must be the current manager root, not a
child borrowing its authority. Full is captured before waits and rechecked at commit.

The member must still be idle with completed or absent local work, settled native
ownership, no unfinished shared claims, and no pending or uncertain input, calls,
settings or test environments. A new review cannot erase a changed retirement
binding. Ask mode, independent roots, user-detached members, stopped/paused/failed
work and unavailable proof retain the existing human-instruction path and other
guards. Readmission does not resume a task, change permissions, reset limits or
usage, or replay suppressed/unknown work. After its confirmed receipt, send a
separate deliberate follow-up through ordinary `coordination.send`.

Lost replies retry the same operation ID. A stale review needs a fresh
inspection; a failed durable save restores the target and assignment together.
No running work is stopped to make adoption possible. A known parked idle worker
may retire through its ordinary acknowledged release path; every binding is
checked again and the native ownership lease is held through the membership save.
Foreign native writers remain protected even when the UI last reported idle. Cross-workspace takeover,
automatic enrollment and cross-engine history conversion remain unsupported.
