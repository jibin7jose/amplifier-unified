# Managed static publishing

Publishing packages a project's built static output into an immutable release,
previews it on the server, records review of that exact version, and explicitly
activates the reviewed release. The Publishing settings page and agents use the
same `publishing.*` actions and task ownership checks.

The default target is **loopback only**. Its URLs are reachable on the computer
running the service. A browser on another device cannot use those URLs. There is
no public hosting, authentication gateway, arbitrary application process runner,
cloud account configuration, or automatic restart of listeners in this target.
Static browser applications are supported; server-side application processes are
not. Publishing a site is separate from saving a conversation artifact.

## Workflow

1. Run the project's existing build with its documented tools in the task's
   execution folder. Build execution and its dependencies remain the project's
   responsibility. `publishing.build` packages the resulting directory; it does
   not run commands or install dependencies.
2. Call `publishing.build` with a relative `sourcePath`, a `siteId`, and a new
   `requestId`. Only regular, bounded, visible files are accepted. Symlinks and
   hidden/private paths are rejected, apart from an empty root `.nojekyll`
   build sentinel that is retained but never served. The release identifies the exact
   content manifest and copies the bytes into private managed storage. Source
   files remain unchanged.
3. Preview the release with `publishing.preview`. Inspect the content and any
   interactive behavior; a successful HTTP response alone is not visual review.
4. Record findings with `publishing.review`, specifying that exact `releaseId`
   and a review note. Recording a note does not independently verify its claim.
5. Inspect `publishing.list` or `publishing.status`, then explicitly call
   `publishing.deploy` with the reviewed release and site's `expectedRevision`
   (`0` for its first activation). The result records the target, access policy,
   immutable release, revision, and URL.
6. Build and review a new release to update the site. `publishing.rollback`
   restores a release previously deployed to the same site; it does not rebuild.
7. `publishing.stop` closes the site's listeners. `publishing.remove` removes
   the deployment. Both preserve sources, releases, reviews, and receipts.

Each mutation requires a stable caller-generated `requestId`. An exact retry
returns the recorded historical outcome without repeating the effect. Reusing
an ID with different arguments fails. A historical success can contain a URL
whose listener has since stopped: use status for present liveness. Every change
to a deployment requires its current revision, including stop and removal.

The app durably binds each build request to its original execution folder before
invoking the publisher. Exact retries can retrieve that saved result after a
handoff or source removal. Interrupted admission without a library receipt is
reported unknown and never replayed. Existing local releases remain controllable
after the task's execution folder moves to another host.

Lifecycle logs are durable operation receipts, not captured command output or
HTTP request bodies. They contain no build environment or provider credentials.
The app's data directory retains private publishing state. Chats owning releases
can be archived; destructive chat deletion is refused so audit ownership is not
silently lost.

## Recovery and separation

Interrupted operations retain an unknown outcome and are not replayed. On
restart, prior listeners are not treated as live. Inspect the status and receipt
before deliberately stopping or removing an uncertain local deployment. A new
deploy requires a fresh explicit action against the current revision.

The portable `amplifier_publishing` package owns immutable files, lifecycle
transitions, receipts, and the loopback HTTP adapter. It imports no app code.
`amplifier_web.publishing` adds task execution-folder confinement, agent calling
task checks, local-host metadata, and action schemas. The frontend calls those
same actions. No content is served from the app's authenticated origin.

Additional hosts require a separately qualified adapter with truthful access
policy, immutable release identity, deployment inspection, safe retries,
rollback, stop/removal, and restart reconciliation. Unsupported remote targets
must not be presented as working merely because local packaging succeeded.

## Explicit remote targets

The [private service and SSH transport](PUBLISHING-TARGET.md) connects the same
actions to a separately installed managed process with a private Unix admin
socket. The app never installs a service, starts a tunnel, changes a firewall or
proxy, or saves SSH credentials. Existing host SSH configuration supplies access.

The Publishing page and agent action catalog expose identical controls:

1. `publishing.target.save` stores a task-scoped target ID, label, SSH hostname,
   optional username, absolute Python and socket paths, expected numeric bind,
   and expected configuration revision (`0` for new targets). Saving and listing
   configurations make no network connection.
2. `publishing.target.inspect` reads actual protocol, bind, access policy and
   durable service UUID. The inspection belongs to that exact configuration
   revision. It neither publishes nor proves client reachability.
3. `publishing.target.select` explicitly selects the inspected configuration
   revision and service UUID. New mutations require that selection. Read actions
   can name an inspected target without changing the task's selection.
4. The existing lifecycle actions accept an optional `targetId`. A new remote
   mutation also requires `targetRevision` and `serviceId` from the caller's
   inspection. The server checks those observed values before admission; a
   target ID reused for a changed configuration cannot receive a stale panel's
   files. UI and agent callers use the same fence. Every mutation
   freezes the target configuration and service identity in the app database
   before capturing bytes or contacting SSH. UI actions include the target ID;
   omitted IDs use the selected target for a new request. Exact retries always
   use the first admitted target, even after the selection changes. Pre-existing
   local request IDs remain bound to the original local publisher.

Remote `publishing.build` captures already-built local output in a separate
immutable store scoped to the remote service UUID, then imports only the exact
manifest and base64 file bytes. It sends no source directory path, conversation,
settings, environment or credentials. Captures do not appear as local-target
releases. Build tools still run separately, in the task's execution folder.

The app retains the exact transfer digest and result. A lost response becomes
unknown; another explicit retry only reads the original service's receipt.
An absent remote receipt does not authorize resending. Local capture failure is
also retained before any import. The service checks its UUID on every operation,
so replacing an endpoint between inspection and submission cannot accept work.
Reconciliation also requires the service's durable digest of the exact submitted
RPC, including that UUID. A matching request ID, site or release alone cannot
prove matching review notes, revisions or bytes. Older unknown records without
that proof remain unknown; their identity is never relabeled using new arguments.
Receipt adoption also rechecks the returned URL and access policy against the
original target, including loopback-only previews. A failed reconciliation read
or malformed result cannot turn an uncertain operation into a known failure.
The error response preserves the original unknown receipt and its diagnostic;
the UI retains the exact pending request regardless of HTTP status until an
authoritative outcome is observed or the user explicitly acknowledges unknown.

Older private-target stop/remove records can contain a `loopback-only` label for
a site that was never deployed. Compatibility accepts that label only on a
stopped/removed non-preview site with no current or historical URL, explicit null
current/previous release IDs and an empty deployment history. Those audit
records remain unchanged; a new stop/remove records the configured policy.
Any serving endpoint, deployment history or preview still requires its exact
target policy.

`publishing.target.remove` unregisters an unused, unselected configuration and
retains its history. A target with retained lifecycle requests cannot be removed
or edited into another endpoint; use a new target ID. A used target's inspection
cannot silently accept a replaced service UUID. These guards preserve controls
for retained releases, deployments and receipts. Removing a site never removes
its historical ownership records. Remote connection failures are reported
directly; the app does not substitute an empty site list or invent liveness.

Private app backups contain target configuration, request bindings and immutable
local capture stores. They do not back up the remote service's deployment and
review state. The remote owner must preserve both the Publisher store and its
sibling service-state directory together. Task transfer must retain publishing
ownership on the source or refuse unsupported transfer; copying conversation
text alone does not migrate these controls.

A remote loopback URL names the remote host. To view it elsewhere requires a
separately authorized tunnel preserving the numeric host and port, or an approved
private serving route with its own access/TLS acceptance. Neither target
selection nor a successful deployment receipt proves that route exists.

## Finite release bodies: library support and remaining integration

The portable Publisher now supports prospective body policy, one explicit pin
primitive, current availability and availability-aware backups. These are Python
library APIs; automatic expiry, the app's Storage/Publishing controls and remote
protocol support are not yet connected. Existing app behavior stays in place.
See [the library contract](../amplifier_publishing/README.md#prospective-body-retention-and-pins).

Under an explicit future policy, two prior successful releases have a default
30-day automatic rollback window. `previousReleaseId` remains historical; current
availability and `rollbackCandidates` determine what controls may offer. An
explicit pin keeps a selected body beyond that window. Legacy durable releases
are never opted in by a duplicate build. A new deliberate build after expiry can
materialize identical content as a new payload generation, without changing the
immutable release identity or any earlier receipt. Reads and exact retries cannot
restore it. Deployment removal itself continues to preserve release content.
