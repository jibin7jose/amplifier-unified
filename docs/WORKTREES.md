# Managed checkouts and task execution handoff

The Session settings page and app_control share worktree.inspect/list/create,
attach/status/remove, handoff and reconcile. For clean creation use
`worktree.inspect {mode:"clean", ref:"..."}` and pass its `expectedCommit` to
create or commissioned `execution`. This resolves and pins a commit without
scanning unrelated local changes. An optional creation `ref` must still match
that commit. Legacy `sourceRevision` remains an opaque full working-folder
snapshot digest, never a Git SHA; its existing validation is unchanged.
Clean committed checkout is default; including uncommitted
changes is an explicit option. The reusable library contract and limits live in
[amplifier_worktrees](../amplifier_worktrees/README.md).

A task keeps its canonical session.workspace, native identity, history, settings,
voice records, task goal, drafts and artifact references. Its workingDirectory is
the execution folder. Both paths and executionRevision appear in shared status
and UI. Handoff does not relocate sidebar projects or copy/migrate native session
metadata. Configuration remains scoped to the original home; the process cwd and
PreparedBundle session_cwd use the explicit execution folder. Naming receives the
canonical history-home capability so it cannot create a second metadata owner.
Existing file/tool permission boundaries continue to apply.

Handoff returns a durable pending receipt, blocks new task input, and requests
cooperative release through the existing Foundation owner protocol. A running
writer checkpoints and stops before release. An already parked runtime is safe
to close while a temporary native session lock proves writer ownership. Another
application's owner is not silently taken over. No provider input is submitted,
no effects are rolled back, and no uncertain operation is replayed. After release
the app changes only the execution association and increments executionRevision.
The original task resumes only on a later explicit input or separately reviewed
authorized scheduler admission. Internal handoff does not increment the user stop
epoch. Scheduled work must bind executionRevision and defer configurationBusy.

An initial writer-ownership refusal before any release request is rejected: the
source stays unchanged and that attempt leaves no execution fence. Release
failure after that boundary, process death or an interrupted commit is visible as unknown.
Previously unknown receipts are not reclassified by this distinction; a newly
rejected reconciliation leaves the original unknown receipt fenced.
Restart never resumes the handoff automatically. Reconcile requires current
receipt revision and a user's finding; choose the old source or intended target,
confirm cooperative writer release again, and record a new receipt. Agent-mediated
reconciliation must cite a genuine user message after the uncertain attempt.
A duplicate command returns its original record. Source/target files remain
untouched by handoff. Cleanup refuses folders still used by a task or unresolved
handoff, then relies on the library's exact Git ownership/cleanliness checks.

P01's optional operations source registry exposes handoff receipts read-only as
worktree:<receipt-id>; it does not create a second lifecycle journal. These are
local execution-folder controls. Cross-host filesystem transfer, native session
migration, portable absolute-path rewriting, submodule state copying and arbitrary
Git operations are outside this feature.

Tests use actual temporary Git repositories for staged/unstaged/untracked copy,
conflicts, stale revisions, branch ownership and protected cleanup. The actual
Foundation release handshake and native SessionStore resume are tested separately
from the deterministic browser runtime. The browser exercises the real Git
library/app/controller, handoff/reload/unknown/reconcile, cleanup and mobile UI.
Live provider execution in a moved checkout and cross-device behavior remain
integration acceptance, not claims from those fixtures.

Every managed Git invocation disables configured clean, smudge and process filters,
including inspection, checkout, patch application and cleanup. Source/global config
is unchanged. These operations preserve raw Git and worktree representations rather
than running conversion programs: clean LFS checkouts contain committed pointers,
while explicitly carried local materialized bytes remain local changes subject to
the normal size limits. A filtered repository may consequently look dirty under the
raw-byte inspection even when ordinary Git hides its conversion difference. This
policy prevents passive inspection and managed creation from running repository
conversion commands or downloading external content implicitly.

The runtime admission lock consults an authoritative host execution-state reader.
Pending and unknown handoffs fence starts and new commands until the saved target
and revision commit; a stale queued start is also rejected after the commit. This
applies to generic runtime inspection, task controls, schedule preparation, warming
and retired-worker reactivation, not only the Send button. Approval responses,
interruption and shutdown remain available for a still-owned runtime. The guard
uses the existing handoff records and never treats mutable caller arguments as
permission to clear an unresolved handoff.

The handoff's own settings fence must not cancel the root turn that requested
that move. A new receipt binds its current worker instance, native generation,
input identities and configuration revision. While that exact receipt is pending
before release, only that same root turn may pass model admission to finish.
The source folder, checkout association, execution revision, task, latest human
input and Stop epoch must still match. All model budgets remain enforced; new
input, child calls, unrelated settings holds, replacement workers, older receipts
without this binding and unknown handoff effects remain fenced. This exception
neither releases the worker nor declares a checkpoint: the existing native
boundary still waits for generation completion, tool replies, children and saved
history before placement can commit once.

Explicit Stop supersedes an unperformed move. Its receipt becomes rejected, the
source and completed effects stay intact, and that move's fence is released.
It does not automatically resume the task or replay the move after updating.

Execution host association is explicit in `worktree.inspect/list` (`currentHost`
and `executionHost`), newly created/attached checkout records, task execution state,
and handoff receipts. The host descriptor uses the existing Foundation owner
hostname: `{scope: "local", id: hostname, label: hostname,
identitySource: "foundation-owner-hostname"}`. It survives app/worker restart on
the same named host; it is not a hardware identifier, global machine identity or
cryptographic attestation. A server instance identifier is separate and ephemeral.
The actual release receipt additionally preserves the released writer (if one
was active) and the temporary held Foundation proof owner's hostname, app, process ID and acquisition ID, excluding username/home details.
An explicitly different recorded host blocks mutation/handoff; inspection remains
available. Older records with no recorded host retain unknown provenance and are
not rewritten. For separately paired hosts and canonical history migration, see
[task portability](TASK-PORTABILITY.md); local handoff itself does not select a
remote execution host.

## Commissioning-manager placement

An authenticated direct commissioning manager may inspect, create or attach a
checkout for its currently bound member and request that idle member's handoff.
The original creation receipt and active membership prove that relationship;
nested managers can place their own members, not arbitrary assignment peers.
The same native settlement, writer-release and durable placement owner applies.
The manager does not acquire arbitrary peer controls, cleanup, configuration or
uncertain-handoff reconciliation authority. Independent peers are excluded.
The member must be idle with settled settings; Stop and failed work must be
reconciled through their existing controls first.

The receipt captures the manager's transport-bound input ancestry, assignment
revision, human/task/Stop boundaries and the member's existing placement guards.
Checkout preparation revalidates that captured scope after repository lookup,
Git lock waits and source inspection. The first durable checkout record is
committed together with that check under the app state lock; slow Git runs
outside it. A cancellation before admission creates no checkout. After
admission, the exact operation settles with retained evidence even if its caller
stops, and no later work is authorized by that receipt. Inspect/list/status
revalidate the relationship and manager instruction before publishing results;
manager status reads cannot reconcile a member's uncertain archive.
Authority is also rechecked before release and before committing placement. The
same objective may continue in later manager turns, and unrelated assignment
updates do not cancel an accepted placement. The
manager receives a reserved, guarded outcome notification, including unknown or
rejected outcomes when its original authority remains valid. The member receives
no new input and no replay. A new brief can run only after confirmed placement.
Retries read the original receipt; restart retains unknown effects and does not
redo a move with uncertain writer release. History, drafts, usage and task state
remain with their existing owners.
