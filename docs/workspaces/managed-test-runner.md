# Managed test runner

This extends the existing task environment and queued-check contracts. The host
owns setup, admission, preparation, execution and evidence-preserving cleanup.
The user and agent use the same actions. A browser tab or model turn is not the
lifetime of an accepted check.

## Setup and readiness

`workspace.environments.setup` defaults to an existing Docker service and
installs the exact qualified deterministic tool dependencies into app-owned
storage. This is the Compose-based **Smart Tool DTU** library, not the legacy
Incus bundle or a raw CLI replacement for host controls. Choose `backend: incus`
explicitly for specialized native workflows. Registered remote machines remain
Incus-only; remote Docker registration is not supported. A default never converts
an existing backend, resource, request or unknown outcome.

Setup accepts a backend choice and stable request identity, never an
arbitrary executable, image, socket, command or credentials. Setup does not
install a container daemon, alter firmware, restart shared services or configure
a model provider. Missing platform or backend requirements are reported before
an environment is launched.

Setup is a separate host effect: a direct user action authorizes it, while an
agent needs `test_host_setup` in global scope. This does not grant authority to
change unrelated configuration. Routine task-owned testing needs no separate `test_environments` approval.
Root ownership, Stop and current backend identity still govern checks. Passive
discovery and result reads do not request configuration authority.

Setup retains its identity and phase before effects. Repeating a request returns
its retained result; interrupted effects are observed, never blindly repeated.
Ready requires an actual backend probe. Readiness includes its observation time
and does not claim every project's dependencies are prepared. Upgrading a backend
does not replace the retained backend of an existing environment.

## Runner update policy

The bootstrap revision in `task_environments.DTU_COMMIT` is a reviewed starting
point, not the active installation's update policy. New setup records its
app-owned moving-source provenance. The existing updater installs later exact
commits separately and requires target-local lifecycle proof before selecting
them. Do not add a newer commit to the historical static allowlist: a package
import or Docker image probe does not qualify its library, dependencies or
cleanup. Explicit pins and older retained installations are unchanged.

App-managed runner packages follow their declared moving upstream source under
the global update preference. A resolved commit is an installation identity,
not a standing update pin. Explicit user pins remain explicit. Existing
app-generated pins can be migrated only with an owned setup receipt establishing
their origin; matching a package name or a familiar commit is insufficient.

A runner update installs separately and qualifies the exact package,
dependencies, adapter protocol and configured execution target before selection.
The qualification includes a task-owned source snapshot and actual offline
execution, result retention and owned cleanup. Matching a version number or a
self-reported schema does not qualify a runner. Updating package code must not
rerun host setup, alter a daemon/project/network, or reset operator limits.

Selection is an atomic change for future accepted checks. Existing jobs,
environments, unknown outcomes and rollback references retain their exact
installation, controller, configuration and execution target. A lost reply or
restart reconciles the saved operation; it does not repeat a check or replace an
unknown environment. Old packages remain available until all references are
settled. Each registered machine qualifies its own configured backend; a probe
on another machine is not acceptance evidence.

The updater reports a failed qualification plainly and continues using the
previous runner. Unsupported configured installations remain visible as
owner-managed or explicitly pinned; they must not be reported as current merely
because no automatic update path is implemented.

### Incus engine generations

The Incus engine is a separate update source from the deterministic runner core.
For an app-managed local or registered native Linux target, availability checks
inspect the installed engine and its retained source policy on that actual
machine. Explicit pins and installations without setup provenance remain
protected. Passive discovery does not install a controller or change host setup.

A candidate installs into a private target-owned generation and must finish a
fixed source-copy, offline command, retained artifact and owned-removal check.
Only then can the target atomically select it for future tests. An unchanged
dynamic core can retain its previous proof through a composed receipt recording
the exact core proof and new engine proof; changed code or unrelated settings
cannot borrow those proofs. Original core rollback and accepted-work references
remain valid. Capacity-only edits do not erase a declared moving source policy.

Confirmed no-effect capacity refusals may make a new admission attempt. Unknown
launch or removal outcomes retain the exact original owner and are observed
before continuing. A missing retained artifact fails qualification even when
native absence is confirmed; the proven absent owner releases capacity.

The first implementation removes known settled, unpublished failed or skipped
private runtimes and retains their receipts. Prior selected generations and
unknown installer/guest outcomes remain in the private generation store. Finite
reference-aware retirement of prior Incus generations is not implemented yet.
This differs from core-package retirement and must be reported as retained
storage, not completed cleanup. Native Windows automatic Incus updates are
unsupported; passive Updates inventory still works without POSIX modules.

## Prepared profiles

`linux-python` remains available for short checks without project preparation.
`python-project` and `browser-test` prepare locked project dependencies from the
exact copied source. The browser profile includes Chromium and an isolated X11
display capability, with a longer advertised execution budget. Capabilities name
the supported profile, timeout and resource limits; unsupported lockfiles or
dependencies fail before the check starts.

The normal path is one queued check. When admitted, the host freezes the Git
working area, prepares its dependencies and starts the test environment. It does
not require a user or agent to hand-author private backend configuration or
manually build an image. Preparation runs separately with network access needed
for dependency retrieval, fixed host-owned recipes, no original writable source
mounts and no inherited provider credentials. Test execution is offline. A
private dependency that needs unavailable authentication is a preparation failure,
not permission to reuse an unrelated host environment.

Preparation records the source and lockfile hashes, recipe identity, dependency
inventory, exact image identity, phase and owned builder. These are evidence,
not permission to substitute an image by name. Temporary builders and prepared
images have explicit owners and cleanup outcomes; failed or unknown cleanup is
not hidden as a successful check. Repeated preparation must not accumulate
unowned images or silently replay an interrupted build.

## Durable execution

New queued checks have a guest job identity bound to the existing root, launch,
source and command. The guest supervisor retains process identity, bounded output,
deadline and terminal result independently of the host connection. Commands run
without write access to the supervisor's control records. Lost acknowledgements
and host restarts observe that exact job; they do not run its command again.

Cancellation is a retained request, followed by observed termination of the owned
job or preparation process. A cancelled UI wait is not evidence that the process
stopped. Unverifiable process or backend identity leaves the result unknown and
preserves ownership. Cleanup begins only at a confirmed terminal boundary.
Old operation receipts retain their prior semantics and are never converted into
new executions during recovery.

Two root chats can make independent progress subject to physical resource
admission. Waiting for capacity belongs to the host queue, not repeated model
polling or peer-message exchanges. Completion notifications use the existing
saved subscription and current conversation guard; notification delivery failure
does not rerun a completed test.

## Qualification

Source tests, real container checks, browser controls, and installed adoption are
separate evidence. Qualification includes setup through shared actions, Python
and frontend workloads, the complete long coexistence workload, two roots,
cancellation, a host restart while checking, retained output, notification and
owned resource cleanup. Linux qualification does not imply native macOS,
Windows, WSL or a hosted-runner path is supported. Interactive access to an app
inside the test environment requires a separately qualified preview route;
headless browser tests inside the environment do not establish that capability.

Executable bytecode is part of a dynamic runtime's exact identity, including
cached and sourceless Python modules. Managed helper and snapshot exports disable
bytecode writes so normal use does not alter the qualified generation. The
registered controller runs only the source bytes in its retained verified
manifest; adjacent bytecode cannot substitute for those sources, including when
an older controller bootstraps into its qualified interpreter. These checks bind
application-owned execution evidence; they are not an OS security boundary.


## Remote testing from Mac, Windows, WSL or Linux

A registered Linux machine can be the first and only testing backend. In global
Settings → Testing → Connected test machines, choose an existing SSH connection
to a machine whose native Incus runner is already configured. The origin needs
Git and an SSH client with non-interactive authentication and a trusted host key.
It does not need Docker, Incus, a Linux runtime or a managed DTU installation.
Registration installs only the app's fixed, verified controller in the target
account; it does not initialize a daemon, network, image or project. There is no
new per-check permission prompt.

The origin creates a deterministic snapshot of the selected Git working area,
including dirty tracked and nonignored untracked files. Git metadata, ignored
files, host configuration and credentials are not injected into the guest.
Tracked source remains source: this is not a secret scanner. Source links are
copied as link text without reading their destinations; submodule entries do not
recursively copy other working areas. Snapshot output stays outside the source;
no index, refs, hooks or source files are modified. A changing source, unmerged
index or unsupported file fails before environment creation.

The snapshot exporter is bundled with the app. Native Windows uses pinned Win32
handles, rejects parent junctions/reparse points, and verifies retained authority
records' owner and ACL. The Windows token's user or verified default administrator owner must own those
records. OWNER RIGHTS grants refer only to that verified owner; inheritance-only
entries do not grant access to the current object. Ordinary other users cannot
have write access (SYSTEM and Administrators retain
normal OS authority). POSIX reads and writes remain relative to pinned directory
descriptors, so replacing a parent cannot redirect a source read or receipt.
Git for Windows link-text checkouts and executable index modes are preserved.

Accepted checks retain their original controller, exact target, resource limits,
source hash and cleanup identity. Pausing a machine prevents new selection;
existing work and lost responses retain their original route. A waiting check
may choose another of its already accepted targets before admission. It never
moves after an uncertain effect. Remote capacity, execution and cleanup use the
existing per-machine account authority; no origin-wide pool replaces it.

The origin retains source and downloads verified evidence after target cleanup.
If a download is interrupted, the authoritative target export remains available.
Removing or pausing a current registration does not remove an admitted owner's
retained recovery descriptor. A target whose SSH identity changes must be repaired
through normal host trust; the app never silently accepts a new host key.
