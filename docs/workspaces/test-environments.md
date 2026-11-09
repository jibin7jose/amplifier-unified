# Task test environments

Test environments are an explicitly enabled capability for a managed
Linux host. The workspace controls provide setup against an installed local
Docker or Incus service. They are not a required consumer installation path. The
adapter uses a pinned Digital Twin Universe core in a managed Smart Tool
installation. It does not install Docker, change firmware, configure providers,
or author profiles with a model. Other platforms and a hosted runner are not
qualified by this implementation.

## Shared interface

The user interface and agents call the same host actions:

| Action | Input and result |
| --- | --- |
| `workspace.environments.setup` | Existing backend choice and stable request identity; approved runner installation, offline probe and private configuration. |
| `workspace.environments.setup.reconcile` | Passive inspection of the original setup and its retained effects. |
| `workspace.environments.setup.cleanup` | Remove only verified disposable resources from interrupted setup. |
| `workspace.environments.read` | Workspace and root conversation identity; bounded resources, availability, source location, current observation and retained results. |
| `workspace.environments.start` | Approved profile plus stable `requestId`; immutable source snapshot and one owned environment. |
| `workspace.environments.execute` | Resource identity, exact revision, stable request identity and command; bounded stdout, stderr, exit code and truncation flags. |
| `workspace.environments.stop` | Resource identity, exact revision and stable request identity; observed stopping outcome with data retained. |
| `workspace.environments.exportArtifacts` | The same ownership and revision checks; a bounded verified `/artifacts` archive from a stopped environment, without executing commands, restarting or deleting it. |
| `workspace.environments.finishCleanup` | Exact stopped owner and prior failed cleanup identity; bounded complete archive preservation and confirmed cleanup without replaying the test or original request. |
| `workspace.environments.remove` | The same ownership and revision checks; source and artifact archives retained before observed teardown. |

The resource ledger owns allocation identity and lifetime. The existing operation
request journal owns command admission and results. A duplicate request returns
its receipt. An interrupted or timed-out command remains uncertain and is never
automatically replayed. A read can observe containers without proving that an
earlier command finished. Stop of a conversation revokes agent approval; it does
not itself claim to stop an environment or cancel an in-container command.

A fresh execute request refused at the initial environment lock is a failed
admission, with `status: not-started`, `effectAdmitted: false` and code
`environment_lock_contended`. No command or cleanup starts, and the competing
resource's revision, pending operation and retained preparation remain unchanged.
Inspect that owner before choosing a new request; the refused request is not
replayed. This proof applies only to that new lock refusal. Earlier uncertain
receipts and failures after admission keep their existing meaning, even after
the environment is removed.

A returned `repository-source-changed` or `repository-file-unsupported` failure
during source preparation is a terminal failed check: no dependency preparation, guest launch or test command
started. Keep the source stable, or correct selected paths that are neither
regular files nor symlinks, and submit a **new** check explicitly; the failed
request is never replayed. Reconciliation can recognize an already-retired
allocation from its exact original failed receipt and private owner/backend
binding. This reports the historical no-launch result, not current physical
absence, and performs no cleanup. Missing or changed evidence, pending preparation,
admitted work and genuine unknown outcomes retain the existing recovery rules.

Manual operations serialize for each environment. Queued checks use durable
guest supervision: cancellation requests termination, host restarts observe the
same job, and cleanup waits for a confirmed terminal result. The manual execute
action retains its bounded synchronous semantics. See the
[managed runner contract](managed-test-runner.md) and [queued checks](../QUEUED-TESTS.md).

Routine testing in an already configured managed runner is authorized workspace
work. Snapshot, locked preparation, execution, evidence export and owned cleanup
do not require a separate `test_environments` approval, including in Ask when
needed mode. Ask-mode agents retain their current root execution binding. Full
mode also admits mounted native children, including nested, resumed and persistent
children, through their actual current assigned generation. Trusted system calls
name an explicit owning conversation; they do not invent a native actor or human
input. A system call carrying a native binding must validate it. Task assignment,
placement, owner and backend checks still apply. In Ask mode host setup uses a separate
`test_host_setup` grant. Operator-selected Full access already authorizes setup,
including native descendants and trusted system callers for an explicit owning
conversation. No browser, human input or new approval is needed.
Old test-only capability requests no longer create approval cards; stale cards
are retired without running their operations.

An admitted Full routine operation can outlive its originating turn. Before each
backend effect, it rechecks the captured Full revision, Stop boundary, placement
and canonical execution owner. Turning Full off and back on does not renew that
operation. The conversation and its canonical execution identity continue to own
the environment and request fingerprint. New Full requests separately retain the
actual origin, native principal when present, and assigned generation. A later
authorized child, root agent, system or UI caller can retrieve the exact original
receipt without changing that attribution or replaying the effect. Legacy request
fingerprints and receipts remain unchanged. Different arguments still require a
different request identity.

Passive removal inspection keeps its bounded, read-only ledger and cold-owner
metadata path. A current Full descendant may inspect its owning conversation's
resource; a trusted system may name that Full owner directly. These reads neither
hydrate cold history nor create requests or invoke the backend. Existing passive
root and UI reads remain available without admitting fresh work.

The action interface and durable ownership are independent of the DTU library.
The initial backend boundary is a host-owned `prepare/start/read/execute/stop/remove`
invocation with owner-derived paths, stable identities and bounded JSON receipts.
Only its private helper imports DTU. A future provider must implement that
boundary and qualify ownership, recovery, source fidelity and retention; exposing
a generic MCP endpoint is not an equivalent adapter.

## Profiles and limitations

The fixed Linux Python profile copies the task's actual Git working area into a
separate container volume. Snapshot provenance names its source and immutable
content. A private, durable readiness receipt binds successful source extraction
to that exact universe and snapshot digest. A running container without that
receipt remains unready for checks; inspection never repeats source extraction.
Stop and preservation before removal remain available for partial source copies.
Docker source archives are limited to 512 MiB and checked before dependency
preparation and guest admission. Archives up to 128 MiB use the supported file
upload; larger archives use the supported directory upload with parts no larger
than 64 MiB. The guest checks the complete archive size and SHA-256 before the
same confined extraction. These parts are transport data, never readiness proof.
Successful extraction removes guest transport scratch; the host's immutable
snapshot remains. Failed transfers retain source and partial guest data for
existing inspection and preservation, without automatically replaying launch.
Tracked files and nonignored untracked files are included; symlink text
is preserved without reading its targets; extraction refuses links that leave
the container working folder. Git credentials, repository hooks and
provider credentials are not forwarded. This is not a guarantee that source
files contain no secrets. No original source directory or Docker socket is
mounted into the container.

Docker tests use an internal network, no published ports, a read-only image,
four narrowly selected supervisor capabilities and limits for memory, CPU and
process count. Commands run as an unprivileged user; supervisor records are
private to root. Incus uses its separate unprivileged offline guest boundary.
Container volumes do **not** have a host disk quota. The small Python profile
runs for up to five minutes without preparation. Python project and browser
profiles prepare locked dependencies in a separate owned networked builder,
then run offline for twenty minutes by default (thirty maximum). Preparation
does not receive host credentials or source mounts. Remote browser preview
routing and general multi-service deployment remain unqualified.

Test commands default to a private disposable `HOME` and `XDG_CACHE_HOME` under
`/tmp/unified-test`, created as the unprivileged workload user. They are outside
the retained source/results roots and can be recreated after a guest restart.
Docker's existing profile temporary-space limit applies; Incus uses its guest
temporary filesystem. Explicit command environment overrides remain available.
Keep useful reports, final patches and chosen build outputs in `/artifacts`
(`AMPLIFIER_TEST_ARTIFACTS`); keep package/browser caches, installed harnesses and
pytest temporary fixture trees outside it. Removing an environment still exports
the complete `/workspace` and `/artifacts`; no file filtering or report deletion
is introduced by these defaults.

Stop retains containers and volumes. Remove requires observed stopped work and
successful bounded exports of both `/workspace` and `/artifacts`; failed export
blocks destruction. Archives are retained without extraction on the host. Local
snapshots and receipts remain after removal. Owned dependency images and builders
must also be confirmed removed before their resource owner is retired.

If working-folder preservation fails, **Save artifact evidence** can retain
`/artifacts` independently for diagnosis. Recovery uses the original approved
backend, exact owner, stopped-state checks and immutable request receipts.
It does not mark the working folder preserved, retire ownership, retry the
failed check, or satisfy removal's two-root/full-retention requirement.
Archive bytes, members, logical selected size, safe links and complete tar
termination remain bounded and verified. Incus still streams a bounded full
backup while selecting artifacts only; its input-size, free-space and deadline
limits remain in force. An oversized full backup can therefore still block
Incus artifact recovery. Uncertain exports are retained, never auto-replayed.

## Operator setup and upgrade retention

The supported setup action writes a private `test-environments.json` file in the app data home.
It identifies the exact managed core installation, qualified commit and local
Docker Unix endpoint. The reviewed image digest is fixed by the app. The app
reports disabled or unavailable when this setup is missing or does not match.
Setup downloads approved dependencies after an explicit user action, a scoped
`test_host_setup` agent grant, or standing Full access; ordinary inspection does
not prompt. Agent setup admission checks its real native binding, including a
delegated child's assigned generation. A trusted system can act for an explicit
Full conversation without inventing a native principal or human input.

Accepted Full setup and cleanup continue after the originating turn finishes.
They retain the existing captured access revision and Stop/placement boundary,
rechecking before effects and after final validation before saving the runner.
Revocation, including turning Full off and back on, does not renew accepted work.
An interrupted effect remains uncertain; it is not replayed.

The setup intent's conversation and workspace own its retained result. The
original actor and captured authority remain separate attribution. A later
authorized actor in that same Full conversation can inspect or clean up the
original setup, or explicitly publish its already verified candidate, without
impersonating the original child. The existing exact intent, backend identity,
resource ownership and readiness checks still apply. Reconciliation and cleanup
retain their own current actor; they do not replace the original setup actor.
Ask-mode setup actor checks are unchanged. Routine environment operations use the
shared resource and request ownership described above.

Keep the exact managed core installation and configuration until all of its
owned environments have been removed. Managed uninstall and replacement refuse
to delete an installation with unresolved environment references. Changing the backend pin or endpoint does
not migrate existing resources. The initial adapter refuses to manage them under
a different configuration; operators must retain the approved implementation
needed for their recovery before upgrading. Source tests, controlled-backend
browser tests and real Docker qualification remain separate evidence.

## Finish incomplete cleanup

`workspace.environments.finishCleanup` is an explicit continuation of cleanup,
not a retry of the test. It takes the resource identity and revision, a new stable
request identity, and `previousRequestId` naming this owner's confirmed failure
before teardown. The prior receipt must explicitly record no removal effect,
and either the preservation or verification phase with a safe failure code
(including `preserving` / `retention_storage` when retention space was unavailable), or
the `preparing` phase and `cleanup_helper_not_started` code proving that no
cleanup helper started. Once `Popen` returns, a lost response or helper process
failure remains uncertain; it is not covered by this preflight exception.
A local refusal before the new cleanup helper is invoked stays a failed request;
retained dependency preparation from an earlier start does not make it an unknown
removal. Known local refusal boundaries have bounded reason codes; arbitrary
exception details are not published. This does not settle any earlier UNKNOWN
receipt or change its ownership.
This adds no automatic replay or new automatic continuation.
Stopped inventory alone does not qualify. An unresolved removal or cleanup
executor, including an unbound journal entry, blocks a new destructive identity.
It uses the retained original backend configuration. The failed operation receipt,
test outcome and partial exports remain unchanged.

One older returned failure has a separate, narrow qualification: Docker's
`export-too-large` receipt, containing the exact retained universe and source
snapshot. In the original helper this synchronous export rejection occurred
before destruction, reaped the copy process, and returned through the host's
drain-and-reap boundary. Timeout, restart-only, malformed, unbound and generic
UNKNOWN records do not qualify. The host also requires retained backend,
installation, launch, source and root-owner identities. It records the exact
original receipt digest and those bindings on the new cleanup continuation,
without rewriting the original UNKNOWN. Another unresolved removal still blocks
continuation. A passive read reports this qualification separately from current
physical state and continuation availability; it performs no backend action.

A genuinely interrupted local Docker removal can instead qualify a **new**
cleanup attempt from current observations. The old receipt remains UNKNOWN.
Under the existing exclusive environment lock, the host verifies the original
resource, owner, backend daemon, retained installation and source snapshot; proves
that no matching local helper or Docker child can still act; and observes every
original container in native exited state with no live PID, restart or removal
in progress. It repeats those checks at the effect boundary. Remote or otherwise
uninspectable executors, ambiguous native state, and unbound or still-running
removal requests remain held. Exact bound UNKNOWN receipts for this same owner
and resource remain unchanged and do not block that freshly qualified attempt.

The new request records its qualification and uses its own retention directory.
It must export and verify the current workspace and artifacts again before
teardown; an old export or missing helper file is not proof. A passive recovery
read can advertise `finishCleanup` as the action that performs this inspection,
but does not claim that the executor is settled or continuation is available.

A separate case is a guest whose successful removal is already committed while
only owned dependency preparation cleanup remains pending. The public `remove`
or `finishCleanup` action can finish that preparation through the original
request, not create another guest removal. Both the resource and original journal
receipt must retain matching guest completion, root/resource/backend/source
binding and physical identity. Current authority, Stop, revision and owner-lock
checks still apply. A `guestRemoved` flag without that evidence is insufficient.
This path updates the original cleanup result and never calls guest removal again.

The backend freshly verifies the stopped owner, saves both complete selected
filesystems, verifies their bytes and archive structure, rechecks ownership before
removal, and confirms physical absence afterward. The resource is released only
when guest removal and any dependency preparation cleanup are confirmed. A
missing or changed backend, foreign owner, running guest, insufficient storage or
incomplete export retains ownership and reports the phase that needs attention.

Archive-only retention preserves symbolic-link text and zero-length FIFO entries
as inert archive metadata, including those produced by test fixtures. It never
follows guest links, opens a FIFO, creates a special file, or extracts guest files
on the host. A FIFO entry with a nonzero declared size is rejected. Character and
block devices, sockets, unknown entry types and sparse entries remain unsupported.
Ordinary files-mode and artifact-only validation continue to reject FIFO entries.
These archives are marked `archiveOnly`; restoring them requires an isolated
environment and an explicit safe extraction policy. Member paths, duplicate and
ancestor conflicts, regular-file-only hard-link targets, member count, tar
termination, hashes and size limits remain verified. Nothing is silently omitted
from the selected filesystems.
Docker recovery bounds each selected archive to 4 GiB and reserves space for both
plus 1 GiB; Incus retains its 8 GiB input limit and a 4 GiB selected-content bound.
Files outside `/workspace` and `/artifacts` require the existing full Incus export.

Preservation and teardown have separately retained phase markers. A definitive
export/verification failure now reports a failed preservation phase with no
removal attempted, rather than suggesting a test command might still be running.
For newly queued checks, that known pre-teardown failure admits one separately
journaled cleanup continuation automatically. An unknown guest removal cannot
be replayed manually or automatically under a new identity. The workspace's **Preserve files and finish cleanup**
control and agents use the same action and authority boundary.


## Global queue and execution settings

Settings → Testing & resources → Test execution limits provides advanced defaults
for every workspace on this installation. These settings do not change other
installations' queues or replace shared machine admission budgets.

- Maximum pending checks defaults to 64 and counts all queued, starting, checking,
  stopping and removing jobs. Larger queues use rotating bounded cursor pages,
  with complete journal counts; old waiting jobs cannot hide later jobs.
- Default queue wait is 3,600 seconds. Zero keeps a request queued until Stop,
  cancellation, or its existing authority/source/backend binding no longer holds.
  Per-request `waitSeconds` may override this default, including zero.
- Each profile has CPU cores, total memory, process limit, default/maximum check
  time and dependency preparation time. Temporary-space and shared-memory sizing
  apply to Docker only. Incus currently uses operating-system mounts within its
  total memory limit; separate Incus tmpfs enforcement is not implemented here.
- Check and preparation durations must be positive. The default check duration
  cannot exceed its maximum. Dependency admission waiting and building share the
  saved preparation budget; metadata probes retain their short observation bounds.

Only newly accepted requests use changed resource and timing values. Each queued
job and environment saves its exact resource and timing snapshot; recovery and
cleanup retain the original backend and owner. Lowering queue depth never cancels
accepted work. Queue `expiresAt: null` means no waiting expiry; existing numeric
expiry receipts remain valid. Snapshot timing is separate from resource identity
and dependency recipe/cache identity, so a duration change does not invalidate a
prepared dependency recipe.

`testing.execution.read` is passive. `testing.execution.save` requires the global
`host_defaults` configuration authority and the current settings revision. Ordinary
workspace tests retain their existing task authority. Settings use a private,
atomic store; stale writes preserve the form draft, and an unknown save response
is reconciled by readback without repeating the mutation.

Numeric encoding bounds and existing metadata/archive/export protection remain
fixed. These controls do not delete retained source, evidence, or user data and do
not add automatic retention cleanup. The default is one active owned environment per root. Global work concurrency
settings can raise that ceiling for explicit independent checks or separately
identified manual environments. Physical backend and machine admission still apply.
All retained unknown owners count against capacity until exact cleanup or
reconciliation confirms release. Environment reads support offset/limit paging;
cleanup always names one exact resource and revision.

Environment inspection also recovers a held compute reservation when the original
native owner proves a stopped or absent environment **and** settled launch
admission. Local reads rotate one extra ownership probe through the requested
page. Existing host queue maintenance also rotates through up to 32 canonical
local allocation records, with at most one exact native ownership probe per
30-second pass. This reaches older owners without opening their chats; it does
not scan sessions or history, discover foreign machines, or inspect other
installations. Registered targets perform the same proof under their own owner
lock and release only their own machine reservation. Unknown launches, active
preparation, changed ownership and unavailable backends retain capacity. Merely
reporting `stopped` is insufficient.

A separate `reservationRecovery` observation is saved before release. The original
check, stop and cleanup receipts remain unchanged; freed CPU/memory does not mean
the check passed, files were preserved, or storage ownership was retired. Reads
never retry a test, stop a guest, or remove files. Retained older target controller
generations keep their existing behavior; inspection does not replace them or
redirect historical owners to a newer controller.


### Prepared execution contract

New prepared environments publish `executionContract` in the owned environment
result and in `/opt/unified-prepared/execution-contract.json`. Read it before
constructing a check. Python is on `PATH` and `AMPLIFIER_TEST_PYTHON` names the
actual interpreter; `AMPLIFIER_TEST_ARTIFACTS` is `/artifacts`. Do not assume a
`/workspace/.venv`. The copied source intentionally has no `.git`: the contract
records the original source HEAD, snapshot commit/tree and archive SHA-256 when
available. These identify the admitted snapshot, not later changes in the guest.

For a source with an explicit Python build backend, preparation resolves its
declared and dynamic wheel-build requirements in a separate build environment,
records the exact inventory, and checks prerequisites. It does not install them
into the locked test environment or perform an additional wheel build. The
advertised `pythonBuild.command` builds the current source offline with
`--no-isolation`, writing wheels under `/artifacts/dist`. Virtual Python projects
and Node-only projects explicitly report no Python build backend. A packaging
prerequisite rejection reports packaging unavailable with retained diagnostics;
the already prepared source-test environment remains usable. Cancellation,
timeouts and filesystem/helper failures are not converted into readiness.
The build environment has its own package and byte inventory. Changing build
requirements requires a new preparation; execution never fetches missing packages.
Git-dependent scripts must use the saved snapshot identity or a separately
approved repository-capable route; preparation does not fabricate Git history.

New prepared receipts also select complete archive-only custody on the first
ordinary removal. Both `/workspace` and `/artifacts` are bounded to 4 GiB per
selection and 300 seconds per export on Docker; the existing Incus input-stream
bound remains. This includes dependencies and unknown or modified files, rather
than discarding them to fit the library's smaller default export limit. Archives
are byte-verified, never extracted on the host, and ownership/stopped state is
rechecked before deletion. Insufficient disk space, invalid archives or an
uncertain removal retain the environment. Older receipts retain their original
policy and existing `finishCleanup` recovery path.

Routine queued checks may explicitly inherit the global Storage temporary-workspace
policy at submission. Their full local workspace export can expire only after
confirmed removal and the captured duration; initial source, results and receipts
stay. Manual environments, legacy records, `keepWorkspace` checks and uncertain
cleanup remain protected. See [temporary workspace retention](../STORAGE.md#temporary-queued-test-workspace-copies).
