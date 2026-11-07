# Managed static publishing

`amplifier_publishing` is a standard-library-only component for immutable static
release snapshots and explicit managed listener operations. It knows nothing
about an application, model, workspace, build system, or remote transport. The
caller must first build its site using its existing builder, authorize the source
directory, and scope each call to the authenticated owning session.

Supported runtime hosts are POSIX systems with `flock` and directory-relative
`O_NOFOLLOW` file operations (tested on macOS; Linux-compatible). There is one
active `Publisher` per storage root. Ownership transitions are serialized; body
traversal and unlink run outside that lock. Blocking calls belong on worker
threads. No source or deploy commands run inside this library.

## API

```python
from amplifier_publishing import Publisher, PublishingError

publisher = Publisher(private_storage_root)
release = publisher.build(
    built_output_directory, site_id="example", session_id="task-1", request_id="build-1",
)
publisher.preview(release["id"], session_id="task-1", request_id="preview-1")
publisher.review(
    release["id"], session_id="task-1", request_id="review-1", note="Reviewed the preview",
)
receipt = publisher.deploy(
    release["id"], site_id="example", session_id="task-1",
    expected_revision=0, request_id="deploy-1",
)
site = publisher.status("example", "task-1")
publisher.close()
```

- `Publisher(root, *, bind_host="127.0.0.1", max_files=2000,
  max_file_bytes=20*1024*1024, max_total_bytes=100*1024*1024)` opens storage.
- `build(source, *, site_id, session_id, request_id, retention=None)` returns a
  release. The optional prospective policy is described below; omission retains
  the existing durable behavior.
- `review(release_id, *, session_id, request_id, note="")` saves a review bound
  to that exact immutable manifest. A review records caller intent; it does not
  claim automated visual inspection or security certification.
- `preview(release_id, *, session_id, request_id)` starts a dedicated loopback
  origin for the snapshot. A preview never changes a deployed site.
- `deploy(release_id, *, site_id, session_id, expected_revision, request_id)`
  deploys or updates a reviewed release; an update keeps the current listener URL.
- `rollback(...)` takes the same arguments as `deploy` and requires a release
  that was previously deployed to this site.
- `stop(site_id, *, session_id, expected_revision, request_id)` closes the site's
  deployment and previews. `remove(...)` also marks the managed site removed.
  Both retain releases, reviews, and receipts for audit. A later explicit deploy
  may reuse the site with its current revision.
- `list(session_id)`, `status(site_id, session_id)`, `releases(session_id)`, and
  `receipts(session_id)` return only that session's records. The list retains
  removed sites so their revision and audit history remain discoverable.
- `retention(release_id, session_id)` reads one owned release's current body
  generation, availability, policy, pin and lifecycle revision. It reads no
  content, historical collection or live listener and never changes a lifetime.
- `pin(release_id, *, session_id, request_id, expected_revision, pinned)` changes
  the explicit protection of an available release body, using the current
  lifecycle revision from `retention`. It returns a durable exact-retry receipt.
  Unpinning a durable legacy release does not opt it into expiry.
- `retirement_candidates(session_id, *, policy, limit=25, after=None)` inspects an
  indexed page of prospective bodies, with eligibility, hold reason, current
  lifecycle revision and `nextAfter`. It reads no body or historical collection.
  `policy=None` disables admission; otherwise it uses the policy fields below.
- `retire(release_id, *, session_id, request_id, expected_revision, policy,
  stop_event=None)` admits one eligible local body under the caller's current
  authority/policy. Success means physical cleanup completed; its receipt holds
  compact lifecycle facts. Exact retries preserve the original outcome.
- `reconcile_retirements(*, limit=1, after=None, stop_event=None)` advances a page
  of saved intents. It automatically finishes proven detached private cleanup;
  an untouched original is made available for fresh admission. Opening an owner
  advances one intent. Further passes use `nextAfter`, including past held rows.
  These calls accept a thread event for cooperative Stop between filesystem steps.
- `receipt(session_id, request_id)` reads one existing receipt without a history
  scan. It returns `None` when the request is absent.
- `export_release(release_id, session_id)` returns only `siteId`, `sessionId`,
  `manifest`, `manifestDigest`, and canonical base64 `files`, suitable for a
  transport import request. It reads the owned immutable snapshot through
  no-follow descriptors and verifies the exact exported bytes, including a
  second hash check after initial release validation. It accepts no source path
  and exports no review, preview, listener, or storage metadata. A transport must
  enforce its own message and transfer limits before sending this payload.
- `snapshot(destination)` copies a consistent private database and all available
  immutable release files to a new directory, retaining expired release metadata
  and receipts. It excludes temporary staging and
  owner locks. It does not change live listeners. Restoring a copy never resumes
  historical listeners; callers own backup access, encryption, and retention.
  In-progress/unknown bodies reject a complete snapshot until reconciled; a
  snapshot does not export inode-bound cleanup intents.
- `capabilities` reports `bindHost`, `accessPolicy`, `authentication`,
  `previewAccessPolicy`, and `publicPublishing`.
- `close()` closes owned listeners, waits for active body reads and retirement
  workers to settle, closes storage, and marks running records stopped.
  A context manager is supported.

Explicit operations except `build` return a receipt; recovery returns current
dispositions without rewriting historical receipts. `PublishingError` subclasses
`ValueError` and provides `code` and, when persisted, `receipt`.

## Records and durable retry

A release contains `id`, `siteId`, `sessionId`, `manifestDigest`, `createdAt`,
`files` (`path`, `sha256`, `size`), and `totalBytes`. Its projected `review`,
`previewUrl`, and `previewStatus` describe current related records. Release IDs
are SHA256 of the site, session, and manifest digest; manifest digests are SHA256
of canonical ordered manifest JSON. The captured bytes and original creation
record do not change. Every serve and review verifies the saved content hash.

A site contains `id`, `sessionId`, `status`, `revision`, `url`, `releaseId`,
`previousReleaseId`, `accessPolicy`, `updatedAt`, and `deployedReleaseIds`.
Initial deployment expects revision `0`. Successful lifecycle actions and
listener interruption increment the revision. Reusing an old revision fails.

A receipt contains `id`, `requestId`, `sessionId`, `action`, `siteId`, `releaseId`,
`state`, `createdAt`, `completedAt`, `result`, and `error`. Pending intent is
committed before effects. Ordinary domain changes and their completed receipt
commit in one SQLite transaction. Retirement instead persists its admitted and
detached phases before cleanup, and records current expiry separately from the
historical receipt, as described below. Validation failures are durably recorded.

The caller supplies a stable `requestId` for retries. Its namespace is the
session. An exact retry returns the **same historical result**, including a URL
that may no longer be live; use current status to determine liveness. Reusing a
request ID with a different method or arguments fails. A build request binds the
source directory argument, not its later contents: retrying it after editing or
deleting the source returns the original snapshot. A new build requires a new
request ID. Failed requests retain the same historical failure even when the
condition is later repaired; use a new ID for a deliberate new attempt.

An interrupted process leaves pending receipts `unknown`. They never replay.
A pending deployment, rollback, stop, or remove fences the site. A caller may
explicitly stop or remove the site at its current revision to clear the fence;
the original unknown receipt remains unchanged. A process restart marks formerly
running listeners interrupted and clears their live URLs, preserving old URLs
as historical metadata. Only a new explicit operation can start a listener.

The optional private service adds a separate durable RPC ledger. Its mutation
receipts expose `serviceId` and `rpcPayloadDigest`, the SHA256 of the exact
canonical request including `expectedServiceId`. Remote callers must compare
that proof to their saved admission before adopting an outcome: matching only
the request ID, action, or release can confuse an older request with changed
arguments. Historical receipts do not acquire proof merely by being read.
Their original exact-retry checks must verify the supplied arguments first.
An interrupted RPC without a committed outcome stays unknown, even if an inner
receipt has the same ID. See [the private service contract](../docs/PUBLISHING-TARGET.md)
for the transport and legacy recovery boundaries.

## Exposure and storage boundaries

The default deployment and all previews bind only `127.0.0.1` on ephemeral ports,
separate from the consuming application's origin. An administrator may explicitly
configure a deployment bind address in RFC1918 IPv4 ranges. Wildcard, public,
DNS, IPv6, link-local, and other addresses are rejected. Such a site reports
`private-network`; it has **no built-in user authentication or TLS**. Network
access controls and a managed external service remain the embedding host's
responsibility. A configured but unavailable interface fails to bind. This API
does not create cloud accounts, tunnels, DNS records, or public endpoints.

The HTTP server accepts only its exact numeric host and port, preventing an
arbitrary DNS name from rebinding to the listener. It serves only manifest files,
without directory listings, cross-origin access headers, or hidden/traversal
paths. Responses use explicit MIME types, `nosniff`, same-origin resource policy,
no-referrer, and no-store. Request URLs, queries, cookies, and referrers are not
logged. Stopping a listener closes its accepted connections as well as its socket.

Builds require `index.html`, reject symlinks (including source ancestors), special
files, hidden components, ambiguous paths, excessive depth, file counts, and
sizes. The sole hidden-input exception is an empty root `.nojekyll` sentinel,
which existing static builders emit; it is captured in the manifest but never
served. Storage and source trees must be separate. Files are copied through
no-follow descriptors and are not served from the mutable source directory.

Storage is private host-controlled data: `state.sqlite3`, its journal files,
`releases/<id>/manifest.json`, and `releases/<id>/files/**`. Content files are
read-only and hash checked, which detects accidental changes; this is not a
security boundary against a malicious process with the same OS account. A crash
may leave an unused staged/orphan snapshot, retained rather than replayed or
silently assigned a success outcome. The host controls retention and disk quota.

## Prospective body retention and pins

The Python library implements policy capture, current availability, explicit
pins, chronological automatic rollback choices, backup support, deliberate
recapture, and explicit local retirement with automatic interrupted-cleanup recovery. App
Storage/UI integration and independently installed remote-service support are
separate followups. No current RPC accepts these new Python-only policy or pin
arguments; do not send them to a v1 service or infer its retention support.
The matching service/client can read historical Python `pin` and `retire` receipts, including
after backup adoption, without exposing a new RPC mutation. Older receipt readers
reject that newly opted-in state; keep the matching reader when adopting or
downgrading a store containing those receipts. Existing stores with no new policy
or pins retain their original behavior.

A new `build(..., retention={})` explicitly captures the defaults: `keepDays: 30`,
`rollbackKeepDays: 30`, `rollbackKeepCount: 2`. These optional fields accept integer
values (days 1–3650, count 0–100); `policyRevision` may retain the calling policy's
nonnegative integer revision. No policy is inferred from age or deployment state.
An existing durable identity stays durable even if a later identical-content
build supplies a temporary policy. The policy is part of a new build request's
exact fingerprint; old requests keep their original fingerprints.

A separate record in the same Publisher database owns `policy`, `pinned`,
`revision`, `payloadGeneration`, `materializedAt` and `availability`. The immutable
release ID, manifest, original `createdAt` and request receipts remain unchanged.
For an opted-in or explicitly pinned body, current release projections include
this `retention` record. `retention(...)` also reports the default durable state
for legacy bodies without creating a stored record. `available` reports owner
metadata, not a fresh content-integrity proof; actual content operations still
verify the immutable bytes. Missing or altered content is an integrity error,
never inferred expiry. Reads and duplicate builds do not refresh lifetime.

A prospective deployment records the two most recent prior successful releases
in chronological `rollbackHistory`, with their supersession times and captured
rollback deadlines. Current `status`/`list` expose `rollbackCandidates` and
`previousReleaseAvailable`; historical `previousReleaseId` remains unchanged.
This automatically populated pointer is not an indefinite explicit pin. A pin
can keep an available body offered beyond its automatic time and count windows. Default legacy
sites retain their existing behavior. The automatic window does not prohibit a
new explicit rollback to another still-available, previously deployed release.
Body retirement preserves each promised unused lifetime, current deployment/live
preview, explicit pin and unresolved effect; the bounded
history is not itself permission to delete an older release.

The unused interval begins on materialization, supersession, preview release,
unpin, or release of the last active reader or managed current reference.
Earlier prospective records missing `unusedSince` get a fresh grace on adoption.
Retirement waits until `unusedSince + max(captured keepDays, current keepDays)`
and any captured rollback deadline or longer current rollback promise have passed.
Count eviction never shortens a captured time promise. A stopped/interrupted site
keeps its resume reference; an explicitly removed site releases that reference
and starts grace. Neither `stop` nor `remove` deletes content itself.

One private intent in the same lifecycle record owns retirement. Before detach,
the owner rechecks the revision, actual consumers, pins, unsettled effects and
exact body-root identity, then durably records intent and atomically moves that
body to its exclusive private retirement directory. Unlink runs outside the main
lock through no-follow directory descriptors. HTTP uses a small reader gate over
actual listener state and in-flight reads; it never waits for an unrelated build
or backup transaction. Completed old reads retain their observed release time
until the next owner boundary records it, including inside final retirement
admission. Captured bodies are wholly private
and immutable: deletion does not hash bytes, retain per-file witnesses, or follow
links to external targets. Corrupt bytes and added entries inside that owned body
are disposable; replacement of the root or storage parents is held as ambiguous.
No source path or arbitrary caller-selected directory can be retired.

An interruption preserves `unknown` history and reports current availability and
phase separately. The next owner open/recovery pass finishes a proven detached
body automatically. If the original is still present, recovery restores available
ownership and reports `admission-required`; it never detaches using a saved policy
that may have been revoked. The caller can supply fresh current authority/policy
on its next ordinary pass, without a new human approval. Actually ambiguous root
identity remains held for owner investigation. Stop checks before detach and
between unlink steps; an OS syscall is not a hard cancellation-time guarantee.
There is no scheduler or app policy activation in this library batch.

An `expired` body keeps its manifest, ownership and exact request receipts.
Preview, review, pin and export reject it with `release_expired`; an unresolved
retirement rejects content operations with `release_unavailable`. Old exact
retries continue to return their same historical outcome, without restoring
files or claiming current availability. Backups retain expired metadata while
omitting absent bodies; unknown retirement or missing supposedly available
content still prevents claiming a complete backup. Restoring a backup never
restarts listeners.

A NEW deliberate build may capture identical bytes again after completed expiry.
It reuses the immutable content identity but creates a new `payloadGeneration`,
`materializedAt` and captured policy, preserving original creation facts and old
request results. Omission of policy on that new build captures a durable body.
Unsettled residual content is never overwritten. An interrupted recapture keeps
its unknown receipt; neither a new read nor an exact retry resolves or repeats
that effect. `stop`/`remove` still only affect the
managed deployment and listeners; they do not delete release content or classify
legacy bodies as temporary.

A review remains bound to the immutable manifest, so a deliberately recaptured
identical body retains its existing review. Deployment still verifies every file;
a new body generation is not a new content identity or an extra approval step.
The exact discovered release ID is journaled before installing a recaptured body.
If interruption precedes installation, the historical request stays unknown and
the body stays expired; if installed bytes remain without a committed outcome,
availability becomes unknown and those bytes are preserved for recovery.
