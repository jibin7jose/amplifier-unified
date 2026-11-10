# Feedback follow-ups

The browser and agent use the same feedback actions. A feedback ID is the original submission request ID, never a guessed repository issue number. The host verifies the current GitHub owner, canonical issue URL and original feedback marker before a follow-up. Saved receipts and audit versions survive restarts; repository text is untrusted data.

The [Full initial-publication path](feedback-excerpts.md#initial-publication-under-full-access)
uses conversation-owned initial files/excerpts and retained standing authority.
Later ordinary files also support conversation-owned Full staging and delivery,
with the existing browser review path retained in Ask mode. Direct text comments/corrections already work without a browser;
the optional comparison UI is not a new prerequisite for those callers.

Under saved Full, root agents, assigned native children and trusted systems with
an explicit calling conversation can publish text comments, correction versions
and issue-state changes without a fresh human request or connected browser.
Accepted writes retain their actual source conversation, runtime principal,
origin and captured Full/Stop authority separately from the remote GitHub account.
Each remote mutation rechecks that authority after preflight awaits. Revocation,
Stop or an off/on policy change prevents a delayed write; an accepted background
operation may outlive its originating turn. Exact retries only read the original
result and do not recapture authority or repeat a possible effect.

Read-only report loading and unknown-original reconciliation do not renew the
original effect's authority. They remain available after Stop or revocation and
retain the current check's caller separately. Local feedback identity, remote
owner/marker checks, revisions and append-only corrections keep their existing
meaning. This does not add submitting-chat ownership to host feedback or change
Ask-mode direct follow-ups. Optional browser comparison remains a separate workflow.

- `feedback.get` reads the original report and bounded comment pages. Use a new request ID to refresh; exact retries return the same snapshot.
- `feedback.reconcile` reads GitHub for an unknown original submission. Only a unique fresh owner/marker match binds the original receipt. No match or incomplete/ambiguous search leaves delivery unknown and never resends it.
- `feedback.comment` appends the explicitly requested text. `feedback.update` adds a correction version as a comment, preserving the original report and maintainer changes. It does not replace the issue title or body. GitHub does not provide atomic conditional issue edits.
- `feedback.close` and `feedback.reopen` change only issue state after checking the revision from a fresh read. Title, body and comments remain intact. This check detects changes before the write; it is not an atomic compare-and-swap with GitHub.

Every mutation requires its own stable request ID. Exact retries are deduplicated; uncertain writes are never automatically repeated. Refresh the report to inspect the current remote result before deliberately starting another change. Corrections and state changes retain author, source revision, prior values and canonical receipt links in local audit storage; full audit bodies are not broadcast in UI snapshots.

The Feedback panel exposes delivery checking, correction drafts and close/reopen. Correction drafts persist per browser. Publishing a correction or changing report state is explicit; editing a draft does not post anything. The product still does not edit third-party comments or silently add post-submission attachments. Reviewed excerpt attachments are a separate follow-up.

## Accepted scope for #294 — October 17, 2026

The product owner accepted reviewed correction comments and separately reviewed
later attachments as the complete scope of #294. Replacing the original GitHub
issue title or body is not required. The Review changes workflow shipped in
0.20.172; #558 delivered later attachments. Together they satisfy that scope.
This decision changes acceptance, not the implemented mutation behavior
or its safety guarantees. Earlier qualification records retain their historical
partial-delivery status; original replacement is no longer outstanding work.

## Review proposed corrections

`feedback.edits.review` is a read-only, durable comparison. It requires an
attached client, the original local feedback ID, a completed `feedback.get`
read request for that report, and the proposed title/body. It re-verifies the
current GitHub account, canonical issue and original submission marker, then
records four distinct versions: originally submitted text, the previously
viewed GitHub text, the latest GitHub text, and the proposed correction.
A revision difference is visible even when the change is only issue state or
other revision metadata. Previously shortened previews are labelled; the latest
comparison text is complete. Repository text is rendered as text, never commands.

The review is private to its canonical client, not authority conveyed by a
writable draft pointer. A copied browser draft may be reviewed afresh against
its completed baseline read, but does not inherit the previous client's review
or consent. Same-client exact review retries return the stored snapshot without
remote effects; interrupted reads fail and can be explicitly refreshed. Drafts,
reviews and correction receipts persist; confirmation is reset when the browser
reloads, the proposal changes, or a new review is requested.

The browser requires a comparison and a separate confirmation before publishing.
Its `feedback.update` carries `reviewRequestId`; the server binds the exact
client, feedback ID, proposed title/body and latest revision. Acceptance consumes
that review once in the same local transaction that saves the immutable mutation
request. Exact accepted retries return the saved receipt; a new mutation ID
cannot reuse the consumed review, even after failure or an unknown outcome.
The existing direct `feedback.update` correction-comment contract remains
available to agents; this new optional review does not turn it into original
text replacement. Every correction still verifies current owner/revision before
posting and uses the existing sending/unknown receipt fence.

**Publishing is explicitly a correction comment.** A late maintainer change
between the last read and POST survives because the issue body/title are never
PATCHed. An earlier change rejects publication and requires a fresh review.
The browser provides **Open on GitHub** for a user who wants to edit the original;
that handoff does not claim to protect concurrent edits in GitHub's UI.
New files continue through the already-shipped #558 attachment review below,
with their own complete manifest and visibility consent. A text review or
correction neither selects nor uploads those files.

Automatic original title/body replacement is outside the accepted scope.
GitHub's [conditional request documentation](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api#use-conditional-requests)
excludes unsafe methods unless an endpoint explicitly supports them; the
[issue update endpoint](https://docs.github.com/en/rest/issues/issues#update-an-issue)
offers no conditional replacement contract. A GET/compare/PATCH sequence, an
owner-marked block inside the same issue body, or a local lock cannot protect a
concurrent remote writer. This implementation makes no such guarantee.

The accepted publishing model keeps corrections as separate comments and retains
the manual Open on GitHub handoff. Any future automatic replacement proposal
requires a new decision; acceptance of correction comments does not authorize
lost updates, implicit overwrites or replay of uncertain writes.

Routine coverage: `tests/test_feedback_edits.py`, frontend Node
`tests/feedback-edits.test.mjs`, and the existing registered `feedback-followup`
browser scenario. The browser uses real AppService HTTP/SSE and React UI with
GitHub intercepted; it proves changed-report comparison, explicit confirmation,
draft reload, append-only publication and original text preservation. It is not
a live GitHub concurrency or native device proof. The two existing attachment
acceptance paths remain independent; #558 is not being redelivered as new work.

## Explicit ordinary-file additions (source candidate)

Host action routing is present in this source candidate. Fresh installed-package
qualification is still required; this is not a claim of delivered recovery.
It does not implement protected original-title/body edits. That earlier
requirement was removed from #294 on October 17; a correction comment is not
relabelled as an original edit. The candidate qualification limits below remain
historical evidence, not claims of additional testing from that scope decision.

Optional `feedbackId` on `feedback.attachment.add/remove` chooses a separate
per-owner, per-original-feedback ordinary-file draft. Full agent/system callers
use the existing conversation owner; other callers use the attached browser
owner. Those namespaces remain separate, even if their text IDs match. Initial drafts, selection
and excerpt consent retain their existing flow. SHA256 is visible for every
follow-up file; no chat attachment or source path is implicitly included.
`feedback.attachments.review` reads the original submitted (or positively
reconciled) local receipt and verifies the allowlisted repository, exact issue,
original marker and current account. It freezes the complete manifest, private
destination, account/issue/repository IDs and retention disclosure. Review
alone is not consent and uploads nothing.

Explicit `feedback.attachments.add` requires the complete exact selected
`confirmedFiles: [{id,sha256}]` set. In Ask mode it also requires the completed
browser review request ID and user confirmation. Under Full, review is optional;
an explicitly supplied review remains a one-use exact destination and manifest
precondition. Without one, the existing upload journal freezes freshly verified
repository, issue, current account and complete file facts before its first write.
These observations do not claim that a human reviewed the files. The host rechecks scope, bytes,
account, issue and private repository before any upload. It creates only
isolated Git Data blobs/tree/root commit/ref and an append-only marked comment
on the same issue. There is no issue title/body/state PATCH, new issue,
main-branch write, comment replacement or asset deletion. The initial upload
transport is not changed. Limits remain 8 files, 8 MiB each, 24 MiB total.
Excerpts are refused in this increment.

Full admission retains the actual calling conversation, native actor (including
a mounted child), origin and existing Full/Stop authority. Accepted background
uploads may finish after the turn completes. Every external write rechecks the
captured revision, Stop and placement after awaits. Turning Full off and back on
does not renew an old upload. Exact action retries return the caller-owned
receipt directly, without rereading or reposting an effect. Browser drafts and
receipts remain isolated from conversation staging.

The same conversation may request read-only reconciliation after Stop or
revocation. It keeps the original upload actor and authority, records its own
caller, and never starts an unattempted phase. This adds no generic GitHub
management or cross-account/issue ownership capability.

Every remote POST persists a sending fence, exact endpoint, input fingerprint
and known prerequisites in the existing private follow-up SQLite audit before
the request. Phase IDs are local audit IDs, **not GitHub idempotency keys**.
Returned validated immutable objects are saved as evidence while the phase
remains sending, before further awaits. A commit is acknowledged only after
its isolated tree, empty parents, exact message and canonical Git object are
validated **and** the exact SHA's account-linked committer is read and verified.
Failed or mismatching account lookup leaves commit unknown and ref/comment
not started. A known returned SHA can later be checked read-only; it is never
permission to create another commit.
Acknowledged phases are retained; sending phases become unknown on interruption
or restart; not-started phases remain stopped. Exact request retries return the
saved receipt with no remote writes. Neither content addressing nor a new
request ID authorizes replay. A frozen uncertain addition blocks new uploads
for that client/report.

`feedback.attachments.reconcile` is explicitly read-only. It verifies exact
known objects/bytes and full manifest trees, expected refs/root commits and
bounded uniquely matching comment marker/body/actor/target. Missing,
ambiguous or inaccessible evidence leaves delivery unknown. Stored files with
unknown or not-started comment delivery are reported separately as partial,
not success or failure. Reconciliation never resumes not-started work; any
continuation requires a separately guarded design.
Concurrent checks merge only positively verified phases into the latest durable
receipt under the service lock, comparing immutable destination, manifest and
phase input/prerequisite bindings. An older absent/inaccessible observation
cannot replace a newer succeeded phase or submitted receipt. Network reads do
not hold that lock, and the positive evidence survives reload and AppService
restart.

Privacy is a preflight observation, not an atomic permanent visibility
guarantee. Uploaded files remain in repository history; local removal does not
delete remote history. Shared snapshots exclude audit bodies, raw bytes,
source paths and GitHub stderr. Browser review/confirmation, persistent pending
intent and read-only checking use the same action identities as agent callers.
Browser staging awaits the host acknowledgement of one intended request ID per
selected file, with its exact submitted name, size, SHA256 and original feedback
scope before effects, including every same-ID check/reselection entry point.
Browser MIME is descriptive only. A failed or uncertain complete-intent save
prevents dispatch. Addition dispatch likewise waits for acknowledgement of its
complete pending arguments.
In-memory File references remain until acknowledgement or observed host staging
evidence. They do **not** survive reload. The client's `stagingReceipts` projection
reads saved local staging IDs/hash/size/selection. Same-client explicit staging
checks retain their original IDs. A newly attached browser after natural reload
is a different client; its inherited selection is read-only, not an editable
staging draft or permission to reselect/upload. Other reports and browser clients
retain their independent drafts.
Manager-owned isolated tests and exact-code/outcome reviews remain required
for this exact candidate; this source candidate is not live delivery
evidence.

### Exact read-only attachment recovery

A new client copied from a previous attach captures exact references in a
host-only client-record field, outside writable view state and public projection.
It captures the source's present canonical staging/operation rows, plus explicitly
saved staging and pending-addition hints. It carries prior references through
later reloads and restart, not a list of ancestor clients or permission to query
their future operations. The original client and duplicated tab may remain live;
neither transfers ownership or shares writable selections. Existing identities,
missing previous records and absent feedback initialization do not capture.

Each reference binds original owner, report, exact ID, action and immutable
fingerprint/intent. Missing pending additions freeze their complete arguments.
They become readable only if a later canonical acceptance matches those
arguments and original owner, including the original completed review binding.
Missing staging hints grant no file link until a canonical row matches the
host-recorded submitted-name/size/SHA binding. Staging records both this submitted
binding and the sanitized stored-name/sniffed-MIME manifest derived from the
actual bytes. Browser MIME and writable owner/lineage fields grant no authority.
A naked review ID grants neither file reads nor reusable consent.

`attachmentDrafts` and `stagingReceipts` remain current-owner projections.
`attachmentRecovery` exposes only validated read-only saved files, uncertainty
hints and a report-local blocker; inherited operation receipts are labelled
`readOnly`. The raw references, private audit and bytes are never projected.
Capture includes active operations and the latest 20 terminal operations, selected
staging and the latest 20 unselected staging rows; explicit pending references
and carried references are retained without silent eviction. Canonical file and
addition-history links remain readable where validated, even after a new draft.

An inherited queued/sending/unknown/partial addition or an unmatched captured
intent blocks new additions for that report, even if writable copied state is
cleared. Independent reports are not blocked. An unobserved intent can remain
blocked indefinitely: no hidden retry, cancellation, handoff or re-upload is
provided. Inherited editable-file reuse remains **unqualified and unsupported**.
When no relevant uncertainty remains, an explicit new draft uses this client's
own new file/request IDs, fresh review and fresh confirmation.

Exact same-payload inherited lookups return existing evidence without scheduling
a run; changed payloads conflict, and missing inherited IDs cannot become new
commands. The only new inherited operation is an explicit read-only delivery
check for an exact captured addition. Its receipt belongs to the current caller;
the addition's owner, immutable arguments and phase fences remain original.
Fresh account/private-destination checks precede existing GitHub reads. Positive
evidence merges into the latest durable receipt under the lock, revalidating the
inherited binding and never downgrading known success or acknowledging unattempted
phases. A non-owner check does not clean up the original staging selection.
No POST/PATCH, new upload phase or automatic continuation is licensed.

### Required manager qualification

The authored Python tests exercise AppService storage with an intercepted GitHub
wire, including both response loss and loss of local evidence/acknowledgement
saves at every phase, multiple blobs, restart without mutation replay,
commit-account failure, opposite-evidence concurrent reconciliation, unchanged
initial upload/correction wire, and forged scope/manifest/hash/visibility.
Corrective falsifiers add exact attach/duplicate snapshots while the original
remains active, late acceptance before/after reload and restart, forged/mutated
bindings, submitted versus sanitized names and sniffed MIME, report isolation,
retained bounded history and inherited concurrent reconciliation without owner
selection cleanup. Old strict phase/response-loss assertions remain unchanged.
`components/web-client/tests/feedback-additions-browser.mjs` is explicitly a **component unit
fixture with a synthetic host**, including complete-intent acknowledgement fences
and copied read-only UI; it is not an installed HTTP/SSE test. These tests have
only been syntax-checked, not executed in this source lane.

The manager must build/install the exact integrated package in the
one retained serial DTU and check authenticated `/api/actions` plus real
`/api/events` against **installed** backend/static assets. Intercept GitHub only,
not host actions or EventSource: observe local staging IDs, explicit review,
confirmation, partial/unknown phase display, client/report draft isolation and
natural reload/duplicate-client reads without replay while the original remains
live. Verify lost staging/addition HTTP acknowledgements both before and after
host acceptance; prove complete hints were already present at attach and preserve
exact IDs across another reload and AppService restart. Missing inherited
acceptance must remain blocked without re-selection or upload. Capture imported package path/version, asset
identity, actual action schemas, HTTP receipts, SSE results and zero runtime/model
calls. Serial full Node and whole feedback Python suites, the current receiving
browser check and both exact-code/results reviews remain parent-owned. None ran
here; fake-host success cannot discharge installed receiving evidence. Original
issue title/body editing was outside this qualification and is no longer part
of #294's accepted scope.


### October 9 attachment recovery review

An unavailable original report now resolves to report-local unavailable evidence;
it cannot abort copied-client attachment or HTTP/SSE snapshots. Read-only display
verification uses a bounded metadata-signature cache, invalidated by changed file
identity, type, permissions, size or timestamps. Explicit staging recovery, review
and upload admission still perform full byte and hash verification. This cache
never confers upload consent or permits replay.

Current DTU qualification includes missing, unsubmitted and disallowed original
receipts, repeated snapshots and byte changes, real authenticated HTTP/SSE copied
client recovery, and the component browser consent/recovery flow. GitHub is a
scripted transport in these checks; no live private-repository upload is claimed.
Earlier failed qualification attempts remain historical evidence.
