# Registered test machines

Global Settings → Testing & resources → Other test machines connects an already approved SSH machine once. Waiting checks may then use an available compatible machine. Pausing a machine affects future admission; it does not stop work already owned by that machine.

## Supported setup

The recommended new **local** runner is the Docker Compose Smart Tool path.
Registration below is a deliberate **Incus-only remote exception**, not remote
Docker support. Run the managed host on the Docker machine to use its supported
local daemon route; do not point a local Unix socket configuration at a remote
daemon or borrow another installation's state. Changing the local default does
not retarget existing registered checks or their cleanup controllers.

The origin may run on macOS, Windows, WSL or Linux with Git and an existing trusted SSH connection; it needs no local Docker daemon or Linux snapshot runtime. Registered targets are native Linux Incus runners using an existing trusted SSH connection, configured qualified adapter, cached approved base image, and Python 3.13 or newer in that adapter. Registration installs only a small hash-qualified controller in private account storage. It never installs a daemon, changes BIOS/virtualization settings, enables SSH trust, or changes system services.

A machine's CPU, memory, free-space and dependency-build budgets are shared by cooperating native clients using the same OS account. Registered controllers also share mutable running/retained/disk admission limits by exact Incus daemon/project/account. Different accounts, older clients, unrelated container workloads, remote Docker daemons and Docker Desktop are not collectively governed by this authority. Current physical observations still include outside CPU/memory/disk pressure.

Running and retained instance counts default to 0 (no count ceiling) on local
and registered targets. Saved positive limits remain enforced; a default change
does not rewrite them. CPU, memory and disk admission still apply where supported.
Automatic machine budgets and configured backend limits remain visible separately. Advanced overrides use revision checks; an empty field uses its default, zero disables that individual ceiling. Lowering a budget never kills active work. Saving limits wakes waiting checks on the current origin; other installations reconsider on their normal bounded retry.

## Identity and recovery

The ordinary job/allocation journal remains the canonical owner. It saves the selected controller, immutable package manifest, backend identity, source and resource/timing requests before source transfer or execution. Endpoint aliases reuse a durable per-account authority ID. That ID is distinct from the OS machine ID, because cloned machine images can have identical machine IDs.

The target executes the actual create/test/cleanup operations and persists its effect receipt before dispatch. Lost replies retain the original target. They never authorize moving the job elsewhere or repeating an unknown create/test. Exact terminal backend observations can settle an interrupted create. Cancellation reaches the original builder without waiting for its long operation lock. Incomplete dependency cleanup can continue through the existing owner-checking cleanup helper; completed cleanup remains an idempotent receipt.

Within one scheduling pass, first-fit selection accounts for the checks already
scheduled against each observed backend and verified machine/account. It debits
running/retained slots and requested CPU, memory and preparation slots before
choosing the next target. Pending memory is counted once; running reservations
are not subtracted again from observed available memory. Disk and load remain
physical observations, not invented per-check storage estimates. The target's
atomic admission remains authoritative, including races with other origins.
Planned demand is transient and never rewrites observed capacity or saved limits.
A prior pass's start may still await its reply after native admission. Its exact
admitted owner is already represented by the backend retained total; only an
unadmitted preparation adds another pending retained slot.

A waiting request is bound only when it can be scheduled. Once bound, its exact
controller and ownership route remain retained; an unknown or already admitted
effect is never moved to another machine. This improves existing first-fit
routing without adding load balancing, new targets or credential forwarding.
Per-chat ordered work and configured concurrency ceilings still apply; explicitly
independent checks may use different approved machines.

A confirmed refusal before admission can return to the queue and choose another approved target. Read-only observations are shared within the scheduling pass, and unavailable targets use bounded backoff. A registration paused while its capacity observation is in flight is rechecked before binding.

Source and artifacts are retained before guest removal. Target exports have exact archive identities; downloads accept only private regular files under the owning allocation, refusing links and special files. Incomplete downloads leave the verified target export available. Controller refresh installs the current verified package for future work while existing owners retain their original package manifest and cleanup route.

## Qualification

Focused tests cover source/chunk retry, dispatch-before-effect, lost replies, returned unknown outcomes, terminal reconciliation, aliases, cloned IDs, cancellations, incomplete cleanup, archive containment, target pause races, settings conflicts and original-controller retention across refresh.

The existing registered Settings browser lane covers global setup, lost registration acknowledgement without replay, dirty drafts, target controls, zero overrides, narrow layout and cold reopen. It uses an isolated protocol fixture; it is not deployment evidence.

Live qualification used two approved native Linux machines and an identical tiny cached-image check containing an eight-second wait. Two serial full lifecycles took **67.746 seconds**; two concurrent lifecycles across the machines took **36.864 seconds**. Source preparation was 2.76–3.24 seconds per check, start 3.66–4.33, command 9.28–9.59, stop 2.18–2.35, and export/removal/download 6.65–17.54. These demonstrate bounded parallel progress for this workload, not general throughput or production load performance.

A separate real AppService/TestQueue run used a controlled capacity fixture for a saturated origin and first target. The second target performed actual source transfer, durable job execution, result delivery, stop, export/download and removal in **32.766 seconds**. The saturated target received only a read-only capacity request. No production policy was changed to simulate saturation.

The first development controller exposed the system interpreter's older archive API. Its stopped guest was preserved and recovered through explicit verified cleanup; the final controller enters the already-qualified adapter interpreter before processing requests. All seven development and qualification guests were confirmed absent after retaining source and results, with no remaining machine reservations for those owners.

## Remaining work

- Approved target provisioning for people without an existing SSH/Incus setup; target registration currently assumes that setup already exists.
- Shared authority across different OS users, older clients, Docker Desktop/VM hosts and additional remote backends.
- More representative workload/load benchmarks and any cost-aware or geographic routing policy. Current routing chooses the first compatible available approved target.
- Automatic removal of historical retained user data is not introduced. Sources, artifacts and recovery receipts remain preserved.

## Connect and check

New registrations use **Connect and check**. The saved SSH connection and target
must already be trusted and configured; this is not automatic server provisioning.
The fixed controller validates its package, native machine/account, backend and
configured adapter. A durable connection request then uses the existing test
scheduler, allocation journal and target admission ledgers for one small Python
check. It transfers only a fixed synthetic Git working folder, checks isolation
and execution, downloads and verifies the exact result, and confirms owned cleanup
before enabling a new machine. No new capacity pool or count-limit exemption is
used. Waiting and unknown outcomes stay with the original owner across restart;
missing acknowledgements are read from target receipts, not re-executed.

Settings distinguishes **Allowed for new checks** from the last completed
connection check. Availability is still observed at each job admission. Historical
registrations remain usable but are labeled not yet checked. Supported profile
names describe controller capabilities; the connection check does not claim every
project's dependency installation has been qualified. Refresh checks the current
controller before replacing the saved route, preserves a concurrent Pause, and
leaves existing owners and failed-refresh registrations unchanged.

Cancellation prevents further test work and cleans only the original owner.
Unknown creation or cleanup is retained and never automatically enables a target.
The existing global settings authority governs these requests; a revoked grant or
stopped authorizing chat permits cleanup only. The global default queue wait and
profile timing are frozen when the request is accepted. Ordinary work and cleanup
continue while a connection check waits for capacity.

App shutdown waits for an admitted bounded operation to settle its receipt. An
update or closing host defers subsequent operations and publication; revocation
or cancellation admits cleanup only. Admission is checked again after read-only
inspection, so an earlier inspection cannot authorize a later test command.

Qualification for this flow includes the real AppService/TestQueue, immutable
remote controller, existing native Incus adapter, one CPU/512 MiB offline guest,
fixed-source check, artifact download and verified guest/lease removal before
registration. The isolated origin used a credential-free test transport to relay
the product's unchanged SSH arguments through an already trusted host connection;
it does not prove direct SSH credential setup inside that origin. Existing
registrations and production settings were not changed. Mounted Settings tests
cover saved progress, lost acknowledgement without resubmission, close/reopen,
named unsaved edits, unknown-result recovery and paused refresh. Portable origin
receipt/source checks also run in the Windows, macOS and Linux CI lane.
