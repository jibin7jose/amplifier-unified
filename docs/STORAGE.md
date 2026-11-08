# Shared session storage

Voice protocol failures and closure keep a separate, local-only journal at
`<app data>/voice-diagnostics/events.jsonl`. It retains bounded categorical error
fields, provider/client event IDs, the provider's terminal reason, local closure
flags, and the last 32 send/ack type-and-ID observations. Human-readable provider
messages are omitted because they can echo user content; context, transcripts,
audio, images, headers and credentials are never selected. The directory is 0700
and files are 0600, rotated before 256 KB with one previous file. These records
are not added to conversation history, app state, or routed diagnostics. Storage
failure cannot interrupt call cleanup. The journal supports diagnosis; an error
code alone does not establish why a provider ended a call.

Unified and app-cli use the same native project session layout:

```
~/.amplifier/projects/<CLI project slug>/sessions/<id>/
  transcript.jsonl
  metadata.json
  context-intelligence/
    events.jsonl
    metadata.json
  unified/view.json
  live-jobs/
```

`AMPLIFIER_HOME` relocates the community root. The CI-supported
`AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH` can separately relocate capture files.
Project slugs follow the CLI exactly, including spaces, underscores and periods.

The community CI hook owns session event capture; Unified reads it rather than
serializing UI progress into another session event log. The host emits the same
prompt-completion event as app-cli at loop-live's final-turn boundary.
`transcript.jsonl` is the sole resume-message authority. Foundation's
`SessionHistoryStore` reads and atomically saves the existing native transcript
and metadata, including CLI-compatible `.backup` recovery. `SharedSessionStore`
retains cross-host acquire/check/release ownership only: new turns do not write
another checkpoint. A native transcript (including an empty one) always wins over
an old common checkpoint. Only a session without native transcript or backup can
explicitly fall back to a legacy checkpoint; old files are retained unchanged.
A busy owner rejects new work. Reads never mount tools/providers, replay work,
emit prompt events, repair source files, or create another conversation copy.

`unified/view.json` contains web presentation: displayed messages, activity cards,
voice presentation and draft/UI associations. It is not the model's runtime
context. CLI projects and top-level chats appear automatically in the workspace
sidebar. Worker histories stay in the same native files and remain accessible
through their parent conversation; independent forks are top-level chats.
Browsing them reads a page of their native transcript without starting a worker;
the displayed history refreshes after CLI changes. The next worker activation
reads the latest native transcript under the shared lock. Warm workers reuse
unchanged mounts; native transcript/metadata/backup changes force a remount.
An external transcript/backup write during an active turn is rejected before Unified saves,
preserving the older CLI compatibility guard.

Saved tool activity uses Foundation's event reader and exact transcript
associations. The native CI path is `context-intelligence/events.jsonl`; the
explicit CI relocation setting is honored and legacy root `events.jsonl` is not
preferred. The UI scans at most 5,000 physical lines or 16 MiB (including malformed and
unrelated records) and retains at most 2,000
compact observations per page load. Truncation, malformed records and backup
recovery are reported; ambiguous or auxiliary activity is not assigned to a chat
turn. Event bodies never become transcript messages. Raw prompts, model reasoning,
API payloads and tool results are not retained in the activity projection.
Missing model-call identity/cost is not estimated from tool activity. These
native activity cards and diagnostics are in-memory only and are excluded from
`unified/view.json`; existing web-owned activity, attachments, canvas references,
voice presentation and drafts keep their existing persistence.

Application-specific settings, command IDs, operation receipts and indexes remain in the app data
directory. Immutable canvas/result bodies live in `artifacts/<sha256>.json`, with
small SQLite references. Live MCP context is separate from static HTML, preventing
every polling update from duplicating an entire application. Reachability collection
keeps saved artifacts and retained results, including transitive references.
Smart Tool result and argument bodies expire after 30 days, 200 retained results
or 32,000,000 logical bytes. This budget belongs to `smart_tool_operations`,
not every operation, command or saved test. It runs during storage upgrade, completed Smart Tool writes, and the existing
hourly Storage maintenance pass while the app is open, independently of the
unused-code cleanup setting. Each periodic pass expires at most 32 settled
results; its candidate index excludes receipt-only history. Queued, running,
interrupted and unknown outcomes, and records without a settlement timestamp,
are excluded. Small execution receipts remain to prevent replay. Bodies still
referenced by another owner remain protected by ordinary resource collection.

Storage accounting distinguishes current logical record/body bytes from historical
archive sizes and allocated disk space. Shared body references can repeat the same
logical bytes; their sum is not unique storage or guaranteed reclaimable space.
`storage.cleanup.evidence` defaults to saved-test receipts; `owner: "smartTools"`
returns a bounded page of Smart Tool receipt/body sizes without opening resources
or starting tools. Oversized or incomplete records remain explicitly unaccounted.
The read does not reset retention, change policy, or delete anything.

| Owner | Existing retention | What remains protected |
| --- | --- | --- |
| Smart Tools | 30 days / 200 full results / 32,000,000 bytes; qualifying writes and bounded periodic expiry | Execution identities; active/uncertain bodies |
| Process/cell operations | Bounded output ring; optional prospective 30-day terminal output expiry, at most eight due operations per pass | Admission/retry identities, outcomes, kernel results/errors, live/uncertain work and unresolved Work references |
| Schedules | 100 terminal runs per schedule | Current schedule, active/unknown runs and command identities |
| Saved tests | Optional prospective final-workspace expiry | Original source, results, legacy/manual/unknown custody |
| Publishing and outputs | No general age expiry | Current/pinned/referenced content; unlink/remove does not imply content deletion |
| Diagnostics | 30 days / 25,000 records by default | Native transcripts and event files are separate owners |

Current policy, grants, Work records and unsettled effects also live in the
`commands` table. A table-wide TTL would remove current authority or let an old
request look new; it is not a retention mechanism.

### Approved prospective retention policy

The operator-approved direction is one standing Storage choice, available through
user controls or an authorized Full agent/system, with no approval per expiry.
The defaults below are approved for incremental implementation; the owner table
above describes current behavior. Approval of the direction does not relabel
legacy records or enable every listed mechanism on upgrade.

| Explicitly disposable kind | Approved default | Required boundary |
| --- | --- | --- |
| Completed operation output and Smart Tool result/argument bodies | 30 days after settlement | Retain small outcome/retry identity and explicit expired detail; existing Smart Tool 200-result/32,000,000-byte budget remains |
| Settled, acknowledged collaboration payloads | 30 days | All notifications, continuations and consumer cursors settled; native conversation input stays |
| New queued-test final workspace copies | 7 days after confirmed guest removal | Capture the selected policy on submission; `keepWorkspace` and longer existing promises stay protected |
| Test source/log/artifact evidence explicitly classified disposable | 30 days after test and cleanup settlement | No durable output, active review, unresolved effect or Work reference needs it |
| Disposable publishing releases | 30 days without a retained consumer | Protect current deployment, live preview, named pins and selected rollback releases |
| Prior successfully deployed rollback releases | Two prior releases for 30 days after supersession | Current release is separate; operators may change the policy or pin more |
| Explicitly temporary outputs | 30 days once unreferenced | Promotion makes them durable; unlink alone is never deletion |

Native history, ordinary outputs and selected durable deliverables remain durable.
Current policy, grants, Stop/execution ownership and unfinished/unknown effects
remain under their existing owners. Legacy compact request identities remain;
no request-epoch expiration is enabled by this policy. Diagnostics retain their
existing controls and 30-day/25,000-record defaults; native event files are outside
these initial batches.

Each new disposable kind records its disposition and applicable policy when
admitted or exported. Enabling a policy affects future eligible work; applying it
to older material requires an explicit scoped classification. Merely viewing a
record cannot extend its lifetime. Retained references and explicit promotion/pins
can protect a body. Owners use bounded maintenance and existing resource/file
reclamation, rechecking policy and references before removal. There is no global
`commands` TTL, duplicate authority store or generic retention engine.

Small receipts retain identity, fingerprint, owner/action, admission/outcome,
relevant revision, uncertainty and availability. Expired detail is unavailable,
not an empty result or permission to repeat the action. Unknown cleanup retains
its exact intent until reconciled. Disabling policy prevents new expiry admissions;
completed deletion does not pretend to roll back. Current logical bytes,
historical sizes and actual filesystem reclamation remain separate measures.

Contract and reader support precede each new deletion mechanism. Independent or
older readers must interpret unavailable detail safely; otherwise incompatible
rollback is unavailable while that policy is enabled. Restoring a backup does not
authorize replay or silently override a completed deletion. Broader storage
redesign and optional versioned request horizons remain separate work.

### Incremental application state

App-state saves use `state_records` for individual session references, per-session
runtime records, top-level settings/catalog fields, and the committed revision.
The `state` row contains only the ordered session IDs and an empty base. A full
save reconciles all records and deletions; a scoped save visits its declared
records. Both compare serialized values and update only records that changed.
Even a caller without a known mutation scope therefore avoids rewriting the
whole app snapshot for a small progress update. Full saves still serialize the
fields they compare; this is not general mutation tracking.

Use `state_records.load()` for every committed-state read, including backup
inspection and artifact retention. Reading only `state.value` is incomplete.
The reader also accepts existing full snapshots with scoped overlays. The first
full save converts those to individual records in the same SQLite transaction
as the revision and private client state. Failed saves roll back together;
completed messages and operation receipts keep their existing commit points.
This does not change canonical event capture, transcripts, or model context.

### Large-chat projection factoring

The save path uses existing `_coldFields` resources in the **stored
projection only** for large messages and retained execution accounting. Live
objects remain mutable. Every eligible save freshly checks complete typed
content, including nested, same-length edits. Normalization and the existing
execution filtering still precede storage; receipts are not discarded.

Unchanged execution may reuse its committed resource after every fragment passes
its bounded content check and the exact resource index and validated backing-file
evidence still match under the SQLite writer lock. Initial file evidence requires
the complete content hash; changed evidence requires canonical preparation and
validation. Compact reference metadata shares the existing encoding budget and
is promoted only after commit. Failed saves, owner replacement, cold/native
transitions and unsupported content cannot acknowledge a new cached reference.

This reduces repeated large-file writes when only progress changes, not all
serialization. Changed content still uses canonical encoding. Restart, private drafts, command
replay, nested edits, accounting and missing/corrupt resources require checks.
Derived message summaries must retire when their mutable body is handed out.

Passive peer notifications load the recipient's messages when appending, while
unrelated cold execution, configuration and catalog references stay indirect.
They still verify native history and reconcile a changed transcript before the
append. The message and receipt share their existing save boundary; an uncommitted
failure restores the append and its navigation/reference metadata, and an exact
retry of a committed notification never wakes the recipient.

Background owners that write or commit through the shared application connection
must use the existing application writer lock, including continuation retries,
queued-test completion/recovery and task creation. SQLite savepoints belong to
the connection: an unrelated commit can end another operation's savepoint while
its file write is awaiting completion. Capture and publish state under the lock;
keep backend calls and worker waits outside it, then re-read current receipts
before publishing. `tests/test_retained_result_writer_exclusion.py` pauses a real
retained-file write and checks concurrent owners, rollback, observer visibility
and exactly-once notification. Extend that coverage when adding a background
writer; do not add a second transaction owner or serialize backend execution.

Do not infer physical cleanup from transaction rollback: a process may die after
writing a file but before committing its index. Resource creation now takes the
SQLite writer lock first; failed writes undo their index savepoint without
committing or discarding unrelated caller work. Maintenance marks the retained
graph once and inspects at most 128 artifact-directory entries per sweep, retaining
a scan cursor. It removes only unindexed hash files and abandoned atomic-writer
temporary files. Missing retained resources or uncertain presentation recovery
block cleanup. Durable root rows are captured on the SQLite owner and decoded in
a worker outside the application and writer locks. Large valid JSON records and
wide live state no longer fail a fixed size/field/time admission test forever.
Live mutable references stay on their owner thread and never hydrate cold fields;
the canonical traversal/cycle guard still fails closed. Live and durable root
costs are recorded separately; the live walk and individual SQL operations are
not a strict wall-clock latency bound.

Before collection, changed durable rows are decoded again off the interactive
thread. Progress-only changes with the same roots can collect. A new immutable
snapshot may also arrive during traversal: up to three catch-up rounds extend
the conservative mark with its complete reference closure. Earlier marks remain
retained for that pass, even when their original roots have been replaced. The
exact final durable snapshot, coverage of every current live/durable root and
all inspected resource index bindings must still agree under writer exclusion.
Continued churn beyond that bound, late changes, recovery or unavailable bodies
preserve all files for a later attempt. Each automatic pass
prunes at most 1,024 indexed resources, commits, then rechecks re-adoption before
removing those files in a worker. Cancellation settles the file worker before
releasing exclusion. Later saves continue the backlog with a fresh proof.
Explicit/offline collection keeps its complete proof and existing batch scope.
The immutable-resource walk also runs outside the application and writer locks.
It captures up to 64 immutable resource indexes per short owner-lock acquisition,
then inspects those files in one settled worker call. This bounds metadata per
handoff and avoids one executor round trip and lock acquisition per retained file;
it does not limit retained files or their size. Each file still uses the existing
verified-edge cache and the final proof rechecks every inspected index. Large
individual files remain linear validation work, and continuous new roots can
still defer a pass at the unchanged catch-up boundary.
Cancellation before pruning does not delete anything; cancellation after pruning
settles the bounded file removal before releasing exclusion. Missing bodies or
an uncertain reference graph defer collection, and navigation need not wait for
body traversal.

Read-only performance diagnostics expose the latest `storageCollection` outcome,
fixed reason code, root catch-up count and completed-pass reclamation counters.
Reasons distinguish presentation recovery, root capture/decode failure, unavailable
bodies, catch-up exhaustion, late live/durable roots, changed indexes and file
cleanup failure. `indexedPruned` is not physical cleanup: `indexedFilesRemoved`
and `indexedBytesRemoved` count confirmed file unlinks after the re-adoption check.
Bytes are logical file lengths, not filesystem free-space gain. Unindexed file
removal has a separate count. Cancellation or failure can leave partial cleanup,
so `removalCountsComplete: false` means zero reported counts are not proof that
nothing was removed. Diagnostics contain no artifact identities, paths or bodies.

Deletion preview remains read-only. The confirmed path reconciles unindexed
files under writer exclusion before reporting file cleanup complete, yielding
between directory batches without surrendering its app/write locks. Staging
rechecks resource indexes so a re-adopted hash survives. Failure leaves the
confirmed deletion's existing cleanup-pending receipt intact. Backups defer
maintenance. Ordinary orphan cleanup is driven by subsequent successful saves,
not an independent idle timer. Crash tests cover process exit around resource
creation, not power-loss or filesystem corruption guarantees.

Shared-history deletion suppresses only the reviewed session identities and
their descendants in that native project. Reviewing all existing history is
still a snapshot: other sessions and later new sessions in the surviving
workspace remain discoverable. Confirmed cleanup-pending and completed receipts
both suppress the deleted identities against a stale catalog and after restart.
Managed-chat deletion retains its separate whole-owned-project scope.
If the disposable catalog becomes unavailable, public counts retain their last
values alongside the unavailable-cache diagnostic until recovery.

The confirmed-deletion gate is deliberately conservative: an active backup or
an uncertain retained resource graph anywhere in the library can defer **all**
physical purge, including the deleted chat's folders and transcripts. The chat
is tombstoned immediately and the receipt reports `cleanupPending`; the files
remain until the blocker is repaired and startup recovery completes. This is a
behavior change, not a promise that only unidentified orphan files are deferred.

Resource adoption refuses existing symlinks/non-regular files and size-mismatched
files rather than silently overwriting them. This is not a same-size integrity
check (cold reads still validate their hash). There is no automatic repair of an
indexed corrupt file in this change. An unindexed malformed file may be removed
by a later safe sweep, but an indexed conflict requires explicit recovery; a
failed save itself does not guarantee that sweep has run.

## Migration and backup

On first 0.8 startup, before accepting requests, Unified preserves the original
`app.sqlite3` in `backups/shared-storage-v1/app.sqlite3`, migrates missing native
transcripts, externalizes artifacts and shrinks the database. Existing shared
transcripts win. Old app-owned session files are left intact. Large installations
can take several minutes for this one-time offline migration.

Normal backups run file copying and compression in a background thread. They
include app configuration (including credentials), artifacts, operation/index
state, and shared root/worker sessions used by this app, including explicitly
relocated CI captures. Automatically discovered CLI entries are a rebuildable
index: listing them does not expand backups, read transcript/event bodies, or run
legacy canvas migration. Opening a chat loads its transcript and lazily scans
bounded CI activity; it does not add that external session to app backup scope. Existing app drafts,
canvas artifacts and indexed diagnostic records remain part of app storage and
backup. Backups do not collect unrelated CLI conversations or external Smart
Tool work directories. Backups are
private but unencrypted. Shared session files survive app conversation cleanup. Removing a chat or resetting
app conversations hides the currently listed chats from automatic discovery; it
does not delete their CLI history. Newly created CLI chats can still appear.
Resetting app settings leaves shared Amplifier configuration and workspace settings untouched.
Backups include the shared global settings, credentials, routing and saved bundles, plus
workspace settings for conversations used in Unified. See [shared configuration](SHARED-CONFIGURATION.md).

Do not run pre-0.8 against the migrated database. To roll back, stop Unified,
preserve the post-migration app directory and shared files, restore the retained
pre-migration database and old app session files, then run the older release.
That database represents the migration-time snapshot, not subsequent work. Do not
roll back or delete shared CLI files as part of an app-only rollback. The settings
rollback button changes ecosystem dependencies, not application storage versions.

### Shared session projection index

Each saved generation builds one transient index of session IDs, direct/native parent candidates, active chats and bounded notification previews. Browser and Terminal projections share those catalog facts while applying their own selection, drafts and notification visibility. Cross-project native aliases still use the canonical parent-matching rules. Every save invalidates this index, even without a revision increment; cached views must never outlive their saved state generation.


## Automatic cleanup of unused code

Settings → Storage → **Automatic cleanup** applies to every workspace on this
app installation. It is enabled by default, with a seven-day period beginning
when an owned code copy is first verified unused, and a 10 GiB size target.
Zero days removes the extra waiting period; a zero size target disables size
pressure. Turning automatic cleanup off leaves explicit cleanup
available. Settings changes use a revision check, and a lost reply is recovered
with the original request ID rather than submitting another cleanup.

Agents, native children and trusted systems with an explicit calling conversation
can save this policy and run it under standing Full access, without a browser,
new human confirmation or mandatory preview. Ask retains the existing user
controls. Accepted explicit work keeps its source authority across ordinary turn
completion, but rechecks the captured Full revision, Stop, assignment status,
workspace and native execution ownership after waits and at each owner's removal
admission. Revoking and restoring Full does not renew an already captured run.

The saved global policy is durable configuration: automatic maintenance continues
after its setter finishes or loses Full. Stop and revocation fence that setter's
in-flight explicit cleanup, not the committed maintenance policy. New autonomous
request receipts belong to their canonical source conversation and retain the
actual agent/system actor privately. Exact retries after Stop or revocation only
observe the original receipt; they do not refresh authority or repeat an attempt.
Global reads and public results omit private authority and input identities.

The policy asks the existing ecosystem-generation and purpose-installed test
runner and qualified dependency-layer owners to remove only copies they can still prove unused. Current and
rollback versions, live generation leases, configurations, observed process
references, local source edits/untracked files/unpublished commits, and uncertain
operations protect a copy. Conventional untracked Python import caches in
`__pycache__` are disposable when their regular tracked source is unchanged:
the verified installed graph or the cache's recorded Git commit must prove the
complete tracked tree. An uninstalled cache also needs a remote-containing ref
or the exact commit-and-source `FETCH_HEAD` receipt left by the updater's normal
detached SHA download. This includes on-disk content that
Git index flags could otherwise hide. Supported CPython 3.12–3.14 cache names
must match their header, have a nonempty payload, and remain regular files beside
the tracked module; symbolic links are never accepted. A cache payload can be
stale, damaged or hand-edited and is still disposable under this rule. Inspection
never imports source, starts its interpreter, or deserializes cached code.
Regular files in `build/lib` are also disposable when byte-identical to a proven
tracked source file at the repository root or under `src`; their conventional
import caches follow the same header checks. Unique or edited build output,
unknown names/producers, files outside these conventions, packaging metadata and
nested environments remain protected. Names alone never establish that a file
is disposable. Owners repeat their checks at removal admission;
links are not followed as cleanup targets. Ecosystem qualification is bound to
a bounded inventory captured before its source checks; a changed inventory or
runtime-sharing decision defers that candidate. The same inventory follows the
retirement rename, and later changes remain in an owned quarantine for inspection. Interrupted cleanup
receipts remain visible and are not permission to repeat an uncertain effect.

**Review unused copies** is an explicit bounded inspection. The ordinary setting
read performs no scan and starts no worker. Each pass inspects at most four
ecosystem candidates, eight purpose-installed runner candidates and eight
qualified dependency layers, with time
limits and a retained cursor. The cleanup policy saves that cursor in its durable
receipt and restores it after restart; a scan position is never deletion authority.
Protected copies include bounded reasons distinguishing unverified source,
unverified extra files, local edits, and a changed or incomplete final check.
Incomplete inspection is labeled; uninspected
copies stay put. File-content measurements are not physical disk-allocation
promises: hard links, shared runtimes and copy-on-write blocks can retain storage.
Storage subprocess I/O and deadlines run independently of the app event loop;
caller cancellation still waits for owned process cleanup. Initial inspection
accepts a completed reference check after delayed delivery. Final removal checks
also bound preparation, queueing and delivery within their existing two-second
budget; an expired check defers removal without retrying. Failed initial reference
checks retain a bounded stage, category and elapsed time in the preview and
cleanup receipt, without settings, paths, process arguments or raw error output.
The size target uses the existing observation ledger's latest measured sizes
across bounded batches. Timestamp-only legacy observations retain their age but
contribute no invented size until measured. Newly protected, absent or removed
copies stop contributing; unvisited observations survive a partial pass.
The preview distinguishes this rolling estimate from the current freshly
verified batch and does not claim a complete inventory or hard disk ceiling.
Only candidates freshly verified by their existing owner can be selected and
removed; an earlier observation is never deletion authority. The age policy
continues to apply independently. Automatic checks run hourly while the app is open and after
existing updater maintenance triggers; update/activation work takes priority.
Successful runner selection only wakes this policy owner; it cannot bypass the
global off setting or retention grace. Explicit user uninstall and Skip actions
retain their separate existing authorization and exact-owner guards.

The policy never deletes chats, project folders, Git worktrees, saved test source
or result archives, the only copy of an artifact, dependency stores owned by other components, or
old application-installation generations. Current, previously selected,
rollback and unknown Incus engine generations also remain retained until their
shared references can be proved absent. Those categories remain inspectable by
the existing Storage tools; user-file deletion keeps its explicit review.

Receipts record the selected file-content total, completed removals and
unconfirmed attempts. They do not claim unknown effects succeeded. Browser
qualification uses the built client and production service with a small
synthetic installed graph: global settings, stale concurrent saves, exact retry
after a lost save/cleanup acknowledgment, real owner removal, saved evidence
preservation, and desktop/narrow draft protection. It makes no claim that a
production installation or a remote runner has been cleaned.

### Orphaned generated worker environments

The same policy also discovers generated runtime `.venv` directories that no
longer have a release or baseline reference. Its bounded inspection retains
current/rollback/leased generations, all surviving release aliases, saved module
source references, installer locks, running processes and changed or unknown
installed files. Only an unused generated environment is detached for removal;
its project source, manifest, lock, qualification and failed-attempt records stay.
The normal age/size policy applies, and a final reference/identity check occurs
at the existing runtime lifecycle boundary. Interrupted removal keeps the same
retirement receipt. A later policy pass can settle that exact detached payload
after matching its saved identity, or settle an empty pre-rename attempt without
replaying the rename. Changed or missing evidence stays held. Original failed
outcomes remain recorded with any subsequent resolution; a new live environment
is never a continuation target. Configuration discovery and content inventories
run outside runtime admission; only their final metadata witnesses are checked
inside it. Ordinary event/history writes do not invalidate source settings.
This is part of existing maintenance, not a new background scanner or scheduler.

### Reusable isolated-test dependencies

The same global automatic cleanup policy also owns qualified source-free Python
dependency images. Their private records live under `test-dependency-layers` in
the app data directory. Only unused, exactly identified images are candidates;
active borrows, incomplete native builds and unconfirmed removals remain kept.
Each borrow remains held until its task image is confirmed removed. Fresh use
invalidates an earlier cleanup selection and restarts retention. Native image
references and the saved cleanup policy are rechecked before removal. Small
ownership, inventory and removal receipts remain after image cleanup. Task
source, output and test evidence are never dependency-cache cleanup targets.
Docker measurements exclude the exact immutable base image; shared blocks may
still remain after removal. Other dependency stores retain their existing
ownership and preservation rules.

Reuse identity excludes only the static application's own version in its Python
project and root lock entry; dependencies, source pins, build configuration,
toolchain and browser manifests still distinguish images. The root application
is absent from the reusable image. Every task installs its current source and
retains exact original manifest hashes and its own package qualification.

### Saved test evidence

Settings → Storage → **Saved test evidence** reads recorded sizes only when the
user chooses **Review saved test sizes**. Each page covers at most 32 canonical
test allocations. Earlier pages replace the displayed page; totals apply only
to the displayed, fully accounted records. Newly saved or changed records can
appear after Refresh. This is not a snapshot of all host storage.

Original source, final workspace, and test results are separate roles. Complete
accounting requires a completed removal receipt bound to the allocation's
resource, root, environment and source hash, with a verified complete archive
selection and matching byte totals. A full-environment archive includes the
workspace and results without inventing separate sizes. Missing or inconsistent
records remain visible as unavailable, rather than zero. Remote receipts say
whether downloaded copies are recorded; a total counts one evidence set, not
every possible local and remote copy.

The `storage.cleanup.evidence` read uses bounded scalar projections from the
existing ledger, opened read-only, with a query deadline. It never reads archive
contents, recursively measures directories, loads chat history, starts a worker,
connects to another machine, changes retention, or deletes files. Recorded file
bytes are historical evidence, not current allocated disk space or an estimate
of reclaimable storage. Source and result archives stay protected. An explicitly temporary final workspace may expire under the policy below; its original size remains historical accounting.

### Temporary queued-test workspace copies

**Expire temporary test workspace copies** is an optional Storage policy, off
until enabled. **Use seven-day temporary test cleanup** enables automatic cleanup
and this seven-day choice in one action. It preserves custom unused-code age/size
settings; merely reading Storage or upgrading never replaces saved disabled/custom
choices. Full agent and system callers can use `storage.cleanup.applyPreset` with
`preset: "temporary-test-workspaces"` and the current `expectedRevision` under
the same standing authority as other Storage changes, without human interaction.
The request keeps an exact retry receipt. `storage.cleanup.save` and the duration
field remain available for other durations or disabling expiry.

The choice applies only to checks newly
submitted through `workspace.tests.submit`; the duration is captured in the
existing job/allocation receipt. `keepWorkspace: true` preserves a check's full
workspace, for example when it contains final edits not exported as results.
Manual environments and existing records without that explicit temporary
choice remain protected. This is one standing policy, not a model approval per
archive. Disabling it pauses expiry; extending the duration also extends earlier
promises, while shortening it applies only to new checks.

The existing Storage maintenance pass inspects at most eight allocation records
and expires at most two exact local `workspace.tar` copies per pass after the
captured duration following confirmed environment removal. Cache size pressure
cannot shorten this duration. Unresolved removal, active preparation, remote
custody, combined full-environment archives, changed/missing source or results,
and incomplete receipts remain protected. Final custody verification runs off
the app loop under the existing environment lock; no archive bodies are read
during inspection. The existing cleanup attempt records preserve uncertainty
across interruption instead of replaying an unlink.

Automatic verification protects any individual retained file over 8 GiB, or a
file whose verification takes over 60 seconds. Those copies require explicit
inspection; this policy does not promise automatic expiry of every large archive.

Original `source.tar`, `artifacts.tar`, removal receipts and historical hashes
remain. A separate expiry field reports current workspace availability; it does
not rewrite the original successful preservation receipt or pretend its original
byte count is current disk usage. Obsolete legacy full copies may still be
removed by a separately authorized, ownership-checked cleanup; their age alone
is not the new policy's authorization.

Test preparation should keep installed dependencies and pytest `--basetemp`
outside `/artifacts`, and outside the source checkout where practical. Put useful
reports, patches and outputs in `/artifacts`, not entire test installations,
large synthetic archive fixtures or copies of previous evidence directories.
Do not bundle earlier `workspace-resources`, qualification archives or `output`
packets into a new source snapshot. Source exports must continue to respect Git
ignore rules; do not blanket-filter tracked files or potential final edits.
