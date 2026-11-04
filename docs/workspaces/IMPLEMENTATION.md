# Implementation and evidence

## Current program coverage — 2026-10-14

Real-provider acceptance found a recipient citing the incoming peer message ID
as result evidence. Reply discovery and the coordination skill now distinguish
current-generation assistant output IDs from request correlation. Invalid IDs
fail before artifact preparation or revision allocation, with repair guidance;
the same checks repeat before commit. Current checkpoint anchors remain usable
outside the display window. Exact terminal resolution and previously rejected
revisions are preserved. Focused source tests cover immediate repair, an
off-lock preparation race, canonical anchors, retained failure and retry paths;
live adoption of this correction is a separate acceptance step.

The integrated source implements the bounded collaboration mechanisms below.
Independent chats remain independent even when they share a folder. The host
owns authority, durable admission and observations; agents choose collaborators,
divide work and judge substantive results. Native history and existing command,
worktree, resource and usage records remain authoritative.

| Approved workstream | Implemented source | Limit or remaining work |
| --- | --- | --- |
| A Authority | Actual root/child identity, intersected file policy, reusable scoped configuration grants, per-chat write settings and inherited-default reset, native approval continuation, delayed revocation and common registered-history scope | Supported native consumers and effect families only; no OS, tenant or arbitrary executable-code confinement |
| B Requests and continuations | Coherent peer creation, retained retry identities, later-generation replies, revision notifications and expiring one-shot watches | Notifications report outcomes; only qualified terminal evidence satisfies a dependency |
| C Bounded messaging | Destination/sender/assignment limits, fair admission, queue expiry, message rates and authenticated peer lineage | Safe tool-boundary informational delivery is adapter-specific; unsupported steering does not become cancel-and-resend |
| D Placement and resources | Explicit per-repository copies, phase-aware handoff, cooperative reservations, owner observations and retained task-owned scratch folders, browser-profile directories and SQLite files | Explicit dependency mappings; no automatic environment inference, running browser confinement, database servers or arbitrary preview discovery |
| E Awareness and cooperation | Bounded active-work packets, advisory file intent, supported file-tool source checks, replacement of volatile guidance and optional shared assignment tasks with atomic exclusive claims | Task claims assign responsibility, not file access; no universal external-writer detection or filesystem fence |
| F Results and integration | Retained exact reported bytes, scoped artifact reads, loaded-origin observations, explicit integration responsibility, recorded candidates and native validation/collection per repository | Qualification covers the chosen commands and observed versions; semantic correctness, complete subprocess imports and publication are not inferred |
| G Recovery and preservation | Durable decisions, expired native approval waits after restart, cleanup-safe bridge waits, phase-aware recovery and complete managed-checkout archive/restore | Archive retains files without reclaiming space or stopping external resource owners; uncertain effects are never replayed |
| H Assignment lifecycle | Explicit commissioned membership, model-call allowances/reservations, usage reconciliation and scoped Stop | An independent mixed conversation has no request-only accounting/interruption adapter; unrelated work is not stopped |
| I Experience and evidence | Shared actions, Workspace work overview, guided review before continuing, shared task list, simpler exact-candidate integration controls, passive inspection, private drafts, per-tab return recaps and portable bounded timing/phase diagnostics | Missing samples stay unavailable; timings do not establish causation, physical paint or complete application latency |

The source mechanisms above implement the bounded October 13 program and the
October 14 follow-up in [PR #706](https://github.com/microsoft/amplifier-unified/pull/706).
[Remaining program gates](#remaining-program-gates) distinguish rollout from
unsupported or optional capabilities. The dated sections retain each slice's
actual revision and evidence limits. Their overlapping test counts must not be
added into a combined-suite result. Qualification below does not establish
publication, deployment or fresh-worker adoption.

## Combined candidate qualification — 2026-10-14

The combined Python source was frozen at `664eb0c3b716` for the broad run.
Final review corrected incomplete integration retries: each unsuccessful parent
attempt now obtains fresh source checks, while completed retries retain exact
results without another command. Four regressions reproduced the defect before
the correction and pass afterward.

Main then advanced to `2631f8b7ea1c` (0.20.134). Candidate `fbfba789a320`
integrates its warmup/history admission and update-cleanup changes, with rebuilt
assets. Six of the 325 Python files differ from the broad-run source: the
integration retry helper and the five upstream version/warmup/update files.
The upstream interactions received an independent source review and a fresh
bounded installed qualification, described below. No second full suite is
claimed for this refresh. All 679 packaged application, static and mapped bundle
files match this final source, wheel and installation. The final wheel SHA-256 is
`a315883bb1d9486ff62b68f2fc082ea0fd01e6baef7b45056ff597eef6f53589`.
Later documentation changes do not alter those application bytes.

| Evidence tier | Observed result and boundary |
| --- | --- |
| Affected installed checks | 1,333 passed in the first run. Two multiprocessing cases failed because pytest importlib mode hid their test modules from spawned children; both passed with the corrected runner. This establishes 1,335 unique passing affected cases across the runs, with no skips. Actual selected Core/Foundation and managed tools were enabled. After the final retry correction, all 144 affected installed integration/artifact/assignment/Operations checks passed with no skips, including the real native shell case. After integrating 0.20.134, the expanded 17-file installed set passed all 426 checks with no skips, covering warmup/history, update cleanup/lifecycle, authenticated worker reads and integration/retained artifacts. These counts overlap. |
| Frontend | 708 checks passed again after integrating 0.20.134; production assets rebuilt from the combined source. |
| Production browser | Resource allocation, return recaps/diagnostics, configuration grants and integration validation passed on desktop/mobile with exact served HTML/JS/CSS hashes. Conversation runtimes were scripted; integration used actual managed shell processes and synthetic approval hooks. An initial integration fixture lacked its Bash dependency; a subsequent attempt exceeded its five-second poll under load. The unchanged rerun passed; neither initial attempt is counted as a pass. After the retry fix, the installed integration journey passed on its first attempt. After integrating 0.20.134, all four journeys passed on their first attempt using the final installed backend, with actual import origins and five served asset hashes per journey verified. |
| New-chat recovery | Passed with Vite and controlled HTTP/event-stream fixtures; separate from production-static and real-backend evidence. |
| Native terminal clients | A separate corrected fixture passed two-client attachment, idle control/resume, duplicate-once admission, detach without stopping work, reconnect with retained history and explicit Stop cancelling the native provider. The original broad-suite fixture remains failed: its scripted provider selected the wrong user message; the qualification copy also waits for native idle before starting its deliberate Stop turn. This is native scripted-provider evidence, not a real-provider terminal run. |
| Real provider and restart | Two fresh runs on the final 0.20.134 candidate passed two collaboration rounds, exact retained results after source deletion, native Allow and grant reuse, commissioned Stop while an independent conversation continued, then disposable-app restart retaining results, decisions and stopped membership without starting workers or replaying work. Each run used four-worker capacity and a one-million-token assignment allowance. |

The full local run at `664eb0c3b716` finished with **7,507 passed, 85 failed,
36 skipped and six passing subtests** in 33 minutes. It is not a green full-suite
result. Each failed case and its causal recheck is retained in the private
qualification receipts; this table reconciles all 85 without erasing failures.

| Full-run failures | Classification and follow-up |
| --- | --- |
| 58 | Reproduced on unchanged main with the same incomplete test environment: 57 MCP subprocess dependency failures and one missing web dependency in a sanitized subprocess. The affected cases pass with a private complete interpreter; actual Core/Foundation origins remain unchanged. |
| 15 | Reproduced baseline platform failures: one case-insensitive filesystem assumption, six macOS Bash installer failures and eight pinned-directory/Git starter failures involving unsupported descriptor paths. No unrelated platform fix is included here. |
| 2 | Reproduced baseline synthetic-fixture failures: the terminal provider selected the wrong user message, and the warm-worker host left required passive bridge requests unanswered. The corrected native two-terminal journey is recorded above; the original tests remain failed in the full-run record. |
| 7 | Lifecycle fixtures used anonymous agent dispatch after caller identity became mandatory. The tracked fixtures now use the authenticated bridge and explicit child inclusion. All 17 lifecycle tests pass on the core branch and final installed package; the product guard is unchanged. |
| 3 | Unresolved broad-run-only failures: one asynchronous artifact-cleanup assertion and two legacy-worker staging/lock checks. Relevant owner/test sources are unchanged, and focused candidate and baseline reruns pass. These observations do not reproduce the original failures on baseline or establish a fix. |

These are bounded journeys, not a universal reliability or performance claim.
Failure injection, two-repository imports, preserved merge conflicts and
connection-loss recovery have their separate native/browser evidence; they were
not all repeated as real-provider variants. Client timing samples cover recap
response delivery through React DOM commit, not physical paint or overall app
latency. Earlier large-catalog measurements remain dated observations, not a
new comparative performance benchmark. Publication, release-channel validation
and adoption by existing installations remain unverified by these tests.

## Explicit integration commands and collected results — 2026-10-14

`assignment.integration` lets the commissioning owner explicitly name a current
member responsible for combining work, using the assignment revision. It never
enrolls independent peers or starts work. Independent conversations can validate
their own candidates without an assignment; an assignment member requires the
explicit responsibility before admission.

`integration.validate` names a saved candidate, one exact repository, command
and timeout. It checks the complete candidate, saves intent in existing command
receipts and submits through the existing Operations request and shell approval
path. Commands are bound through approval; unsupported admission adapters
refuse. The worker asks the host to recheck candidate, Stop, responsibility and
execution after approval, before invoking the mounted shell. This check is an
observation boundary, not a filesystem fence against external writers or a new
sandbox. Operation admission and terminal outcomes remain in their existing
owner. Lost acknowledgment, shutdown and restart never automatically repeat a
validation command.

Explicit `integration.collect` combines at most eight exact invocation receipts
for one candidate. It records native process/owner identity, terminal state,
exit code, output/capture completeness and exact pre/post source observations.
Every listed repository has its own outcome; an unvalidated repository stays
unvalidated. A zero exit without complete native evidence or matching source
does not qualify a passed validation. Current Git HEAD/source snapshots and up
to sixteen exact local result references are retained, using the existing
Outputs store with bounded file preparation and no fixed byte limit per file or
collection. Publication is
unknown unless explicitly reported, with author/reference and an explicit lack
of owner confirmation. Recording a publication report has no external effect.

Read/list and the advanced execution disclosure share these actions with the
agent bridge and attached-session commands. Pending browser request identities
survive reload. Collection does not execute commands or auto-import modules.
Command success covers the named invocation, not the quality of its tests,
semantic correctness of the combination, complete subprocess import closure,
hermetic isolation, publication, deployment or host adoption. Shell subprocess
dependencies remain distinct from previously observed worker-loaded modules.

Qualification: 101 focused checks pass with the selected native Core/Foundation
and managed Bash module, including exact imports from two explicit repository
copies, a real merge conflict preserving both contributors, delayed post-approval
Stop, unknown adapters, partial results, durable files and HTTP/agent parity.
The final current-worker capability/replacement fence has two passing focused
rechecks. The 693 frontend checks and production desktop/mobile integration and
assignment journeys pass. Browser validation uses actual managed shell commands
with synthetic approval hooks; response-loss/reload retries do not duplicate
execution, and saved downloads survive original-file deletion. Served production
asset bytes were checked against the built files. These observations do not
claim provider-driven decision quality, installed-package adoption or deployment.

## Preserving checkout archive — 2026-10-14

Unused app-created checkouts now have separate archive and restore actions.
Archive moves the complete Git worktree into private retained storage, keeping
staged and unstaged changes, ignored and untracked files, symlinks and detached
commits. Git registration retains the commit; no commit or archive is published.
This retires the active checkout without treating unknown contents as disposable.
It does not reclaim disk space or freeze external writers. Clean deletion remains
a separate, stricter action after explicit restoration.

The shared UI, agent and attached-session actions reject attached/active folders,
unresolved handoffs, changed ownership, stale revisions and target collisions.
Durable intent precedes a move; retry or inspection reconciles actual directory
identity and Git registration without repeating an uncertain effect. Browser
retry identity survives a lost response and reload, and restore does not start
execution or alter conversation history. Native Git tests cover actual file/index
preservation, detached history, lost receipts on both sides of the move and
ambiguous replacement paths. All 98 affected installed-package checks pass. Browser checks cover archive, reload/retry, restore,
independent repositories and narrow layouts; the 686 frontend checks pass after
integrating the current new-chat loading change. Its discovery browser journey also passes.
This qualification does not claim deployment, immutable backups or resource
teardown for independently owned processes and services.

## Loaded-source provenance and combined candidates — 2026-10-14

`runtime.provenance` inspects the actual ready worker through its existing
passive dependencies protocol. Core/Foundation origins, mounted object types
and up to sixteen explicitly named project modules come from already-loaded
objects; inspection never imports requested code, installs packages, starts a
dormant worker or unparks it. Bounded file reads run outside the event loop.
Loaded origin, file bytes observed after mounting, and file bytes observed now
are separate evidence. None claims the exact bytes previously imported.
Startup captures only in-memory module and mount identities; it performs no
provenance file reads or background hashing. The explicit inspection supplies
current-file hashes, with unavailable mount-time evidence left unknown.

`integration.capture/check/read/list` reuse existing command receipts. A capture
retains up to eight inspected repository source revisions, optional expected
module origins/hashes and the exact execution binding. A check compares those
same expectations and preserves the capture. Wrong sibling origins or source
drift are mismatches; absent/unloaded modules, changed worker instances or
changing observations remain unknown. Checks do not merge or rewrite either
contributor's work. Repository snapshots bracket worker observation; final
placement and managed-association checks precede the scoped receipt commit.
The canonical worktree owner serializes the final association check against
archive/remove. File/Git work remains outside the app lock.

Stable command retries return retained evidence, including after restart or a
lost acknowledgment. Failed/cancelled preparations create no candidate. Reads
and bounded listing are scoped to the original conversation, shared through
the agent bridge and session HTTP commands. An advanced disclosure in task
execution controls supports the same inspection/capture/check/list/read path.

Bounds are explicit: 64 observed modules, 2 MiB per file, 16 MiB total file
content, a 512 KiB worker report and a 120-second overall inspection deadline.
Git inspection retains the worktree owner's existing file/time limits. Read
workers may finish after cancellation, but cannot write a late receipt. Current
matching files do not prove a hermetic environment, complete transitive import
closure, subprocess dependencies, test success, semantic integration or release
readiness. No project module is auto-imported to make unknown evidence green.

Qualification uses real temporary sibling Git repositories and a subprocess
mounting real Core/Foundation, loop-live, context-simple and an explicit fixture
tool. The native probe identifies its loaded sibling origin, rejects the wrong
expected repository, records the correct candidate, then detects an edited
dependency while preserving both contributor sources. It never calls a model.
Route/restart/failure tests cover identity retries, retained reads after source
deletion, cross-conversation refusal, changed execution/association, malformed
worker output, time bounds and app-lock responsiveness. Production-service
Chromium exercises repository-only capture, passive unknown-worker inspection,
real source drift, immutable reads, reload/list and mobile layout. These are
bounded local and package observations; combined-program qualification and
provider/release/shared-host acceptance remain separate.

Local checks: 274 affected installed-wheel tests and two subtests passed using
the selected native runtime (one explicit optional control probe was skipped).
Final review added private strong-reference comparisons for same-origin module
or same-type mounted-object replacement during detached I/O. The rebuilt wheel
passes all 38 provenance/artifact-runtime cases, including both new races and
the native sibling-repository probe; this helper is the only application file
changed after the broader installed check. All 627 packaged Python/static files
match source. Frontend tests pass (681), and the production-service browser
journey passes with no page errors.

## Retained reported results — 2026-10-14

Fresh `coordination.reply` declarations capture supported local SHA-256 file
references before acknowledgment. The existing output registry and private
resource store own those immutable bytes; request receipts retain only the
declared references, verification outcomes and output identities. Files are
captured in bounded pieces when larger than one MiB, without a fixed per-file
or declaration byte ceiling. Opaque references remain
unverified. The producing generation, execution directory/revision, request
and declaring operation are retained as provenance. This is an exact file
snapshot, not proof of repository integration or semantic correctness.

Large files use a flat manifest of ordinary content-addressed resources in the
existing output registry. The manifest retains the whole-file SHA-256 and byte
count; existing garbage collection follows its resource references. Preparation
uses temporary encoded pieces, verifies the source before/after and rechecks the
declared digest. Admission checks current free disk space; resource pressure
remains an explicit unavailable result, never a successful qualification.
Prepared pieces are adopted in the existing output/reply transaction. Temporary
files are removed after success, failure, or settled cancellation.

Text inspection incrementally decodes the saved pieces, returns only the requested
character range, and verifies every piece and the complete file. Large downloads
first verify into temporary disk scratch, then stream with backpressure. No bytes
are sent before complete verification. Capture, verification, download and GC work
remain linear in the selected file bytes; file bodies do not become resident in
proportion to file size. Manifest metadata grows with the number of pieces.
Existing image/canvas preview limits and output relationship-count policy are
unchanged. This does not add another store or qualify the file's semantic content.

Capture runs outside the service lock, then rechecks generation, Stop, placement
and response revision. Output relationships and reply acknowledgment commit
together. Precommit failures roll back the reply and its output relationships;
postcommit lost acknowledgments return the original identity without rereading
files. Existing resource reachability preserves saved bytes. Missing/corrupt
retained resources never fall back silently to a mutable source. Missing,
changed, over-limit or external sources remain explicit report states and
cannot qualify a successful dependency.

Canonical resource encoding and hashing run in the capture worker. The existing
async resource writer retains SQLite writer exclusion while its disk worker
settles, including cancellation. Artifact API/Canvas reads detach their resource
index, perform file I/O, decoding and hash verification outside the app lock,
then recheck request/revision and current navigation before presenting bytes.
Saved output downloads use the same async reader. No live database connection
enters those workers. Legacy output inspection/image actions retain their prior
implementation; this is not a claim that all output paths have been optimized.

`coordination.artifact` exposes bounded text and the exact saved download through
the agent bridge and per-session HTTP command API. It checks the original
request participants and workspace, including when reading an explicit older
response revision. This does not expose the producing conversation's library.
Related work shows saved-version status, opens those bytes in Canvas and offers
a download. Source editing, deletion or removal of the original working copy
does not change the result. Old replies retain their prior digest-checked source
fallback; they are not relabeled as snapshots.

Qualification after combining with assignment control: 418 focused Python
checks, including real dispatch/bridge/HTTP routes, rollback/lost acknowledgment,
restart, deletion before first open, foreign-reader rejection, stale generation,
placement, Stop and response revisions, total/per-file bounds and corrupt-resource
refusal. The production-service Chromium journey passes two roots and two rounds,
deletion before first preview, later edit/deletion, exact download, bounded agent
read, reload, mobile layout and private-draft preservation. The browser runtime
and terminal anchors are scripted; it does not claim live model cooperation.
Frontend tests pass (681). The prior two browser fixture failures assumed that
reported files still existed and used an unresolved relative Playwright URL;
their logs were retained and the corrected journey passes.

The installed wheel passes 422 focused checks using the selected native runtime,
including four Core/Foundation child-admission cases inherited from assignment
control. All 601 packaged application Python/static files match the candidate
source. This is package and local-runtime evidence, not publication or installed
shared-host adoption; broad combined-program qualification remains separate.

Combined source-candidate capture/check and preserving checkout archive are
implemented in their own sections above. Semantic combined repository/dependency
validation, repeated complete provider journeys and release/adoption remain
separate gates.

## Consistent registered-history scope — 2026-10-14

Native history, recall, coordination list/wait/exact-message/window reads and
applicable shared session-history reads now use the same caller-workspace/all
selector. Explicit all preserves useful registered own-history search without
new grant records or repeated approvals. Waits check scope before loading a
target and after waiting; history/recall recheck after I/O. Recall references
retain the search scope alongside their exact source revision. Human exact-target
UI reads retain existing access; an explicitly supplied scope is honored.

Request results keep exact participant/workspace authority even with all, and
peer output libraries remain caller-owned. Authenticated per-session HTTP paths
retain their exact chosen target. This is a same-user retrieval rule, not a new
secrecy boundary for all app state or an assignment-based access mechanism.
The [route and native-consumer audit](owned-history-scope.md) lists retained
domain boundaries and qualification limits. The focused source suite passed
440 checks; native root/finite/resumed/persistent/nested/in-process recipe tools
and a freshly installed wheel are checked separately. No provider, deployment
or cross-account isolation is claimed.

## Scoped configuration administration — 2026-10-14

Root agents can request explicit configuration effects and scopes through the
existing approval card, then reuse approved authority across turns. Direct user
settings actions remain available. Native app-control requests wait in the
existing worker approval map; Allow/Deny resumes that tool without replaying any
write. Raw API requests retain nonblocking receipts. Advanced settings exposes
the same canonical grant records and revision-checked revocation.

Shared command receipts own grants and decisions. Delayed management/settings
effects retain grant provenance and recheck after waits. Stop, revocation, expiry
and workspace changes suppress unperformed effects; queued configuration plans
survive restart for inspection. Children/new roots do not inherit grants. Peer
input cannot borrow a higher-authority root's grant; saved dependency returns
and watch notifications retain the original input lineage. Unknown legacy
system ancestry does not fabricate authority. Explicitly authorized global
administration works through the actual mounted root tool.

Qualification after the new-chat discovery merge includes 494 affected Python
checks, 686 frontend checks, the production build, desktop/narrow browser grant
approval/reuse/revoke/reload and private-draft preservation. A freshly installed
wheel passes 100 checks including actual Core/Foundation child/root mounts,
native approval waiting, durable grants, shared HTTP clients, schedules and
management. Native orchestrators and browser runtime are scripted; no live
provider or installed-host adoption is claimed. The broader program's full
integration remains its own gate.

The [consumer and read-history audit](configuration-authority.md) names each
covered administration route, alternate settings aliases, live/delayed limits,
and the separate registered-history scope. These are not OS,
universal credential, per-tool or multi-tenant isolation guarantees. Existing
registered own-history access is independent of configuration grants.

## Human-directed member resume — 2026-10-20

`coordination.send` accepts `resume: {sourceMessageId,
expectedInterruptionRevision}` for a fresh queued request from a commissioning
owner to a current member of its active assignment. The host verifies the latest
actual human input against the calling root's admitted input bindings. It must
postdate the recipient's Stop; peer prose, questions and scheduled inputs cannot
authorize resume. `coordination.list` includes the current bounded `signal`,
including `interruptionRevision`, for resident and dormant roots so a manager
can construct this binding without loading history or starting the recipient.
`coordination.context.humanInput` provides the exact latest admitted human message
identity to the current root generation; peer, scheduled, question and stale
inputs are ineligible. It is an observation, not new execution authority. Assignment, task, source/recipient Stop and latest human input
are rechecked during preparation and native admission.

The one-input exception permits a stopped recipient and a paused/blocked task to
receive the reconciliation request. It does not rewrite the task or reset its
saved goal progress. The recipient deliberately resumes its own task after
checking its blockers. Capacity policy and saved turn budgets still apply.
Completed objectives stay completed: an explicit reconciliation/follow-on input
can reach their chat without reactivating the old goal. Independent chats and
failed/unsettled runtimes are ineligible.
The retained receipt consumes this human/member/Stop authorization once, including
unknown admission outcomes; old suppressed or uncertain requests are not replayed.
Assignment admission itself still uses the existing explicit `assignment.resume`.

## Assignment admission and scoped Stop — 2026-10-14

New commissioned roots now share their initiating root's assignment. Nested
commissioning and new-task schedules reserve from the same member/open-task
limits. Historical commissioning records and independent chats are not enrolled
by inference. Existing command receipts own membership, revisions and Stop;
existing execution receipts own call usage and reservation metadata. Native
children use their enclosing root's private model-call admission gate.

Provider admission uses the input of the current native turn separately from
its visible execution ancestry. A delegated worker's return may start a service
observation turn while its model calls remain displayed under the originating
user turn. That observation is not fresh human authority to clear a budget pause.
Child runs retain the input of their spawning call; a later root observation
cannot renew an older child's admission. Existing generation, Stop, settings,
worker ownership and allowance checks still apply before any provider request.

The owner can set aggregate token/cost allowances, per-call reservations and
concurrent-call limits. Admission serializes comparison and durable reservation
before provider invocation. Actual usage replaces each call's allowance without
double-counting; history editing, display reconstruction and later corrections
retain the original assignment binding. Missing usage remains unknown. These
are allowances for supported calls, not exact provider spend caps or limits on
arbitrary external tools, uninstrumented providers or separate realtime voice.

Stop closes assignment admission before requesting interruption of still-bound
roots. Preparation rechecks that authority before creating a root, and delayed
inputs, schedules and replies cannot restart stopped work. Interruption receipts
distinguish returned requests from unknown outcomes; restart does not repeat an
uncertain interruption. Independent chats are never substituted as whole-root
Stop targets. The current adapter cannot interrupt or account for one request
inside an independent mixed conversation. Stop reports that limitation. With
an enabled token/cost limit, queue/steer to such peers is refused; informational
notices remain available, and commissioned helpers share the budget.

Individual requests support `coordination.cancel` from their original sender.
Queued or pending-steer input is withdrawn under the same admission lock and
keeps a negative, unqualified receipt. An in-flight or admitted input retains
its actual delivery state and a cancellation request; every later admission
check rejects it, and the recipient sees the instruction in its next fitted
coordination observation. No worker is started or whole conversation stopped.
The recipient reports `outcome=cancelled` when settled; a report awaiting its
terminal event is distinct from a settled report. Mixed work and completed
effects stay intact. A lost acknowledgement is re-read with the same command
identity. Cancelling closes only that request's future return subscription;
already admitted returns are not interrupted. This cooperative path does not
add request-level model-call accounting or a hard interruption adapter.

Explicit user recovery uses revision-bound `assignment.resume` on the commissioning
chat after interruption receipts, roots, native workers, preparations and current
generations have settled. It preserves membership, task records, cumulative
accounting and limits. It does not send input, resume paused local tasks or clear
interruption revisions. Queued outbound requests and saved waits retain their
original interruption binding; reopening cannot admit old requests or manufacture
late continuation messages. A fresh user request may then start new work.
A historical unknown Stop acknowledgement is not a permanent barrier. On the
same explicit Resume action, the native adapter can prove that the affected
root has no process, preparation, pending admission or unobserved shutdown, and
hold its existing native session ownership lock through the recovery commit.
A live/foreign owner, unsupported adapter, execution handoff fence or outstanding
Stop still refuses recovery. The Resume receipt retains revision-bound settlement
evidence separately; original unknown Stop receipts are unchanged. This proves
current ownership settlement, never rollback or success of external effects.

Separately paused chat tasks and persisted local budget denials remain paused;
the existing task/budget controls own their explicit revision-bound recovery.
Stopped-assignment input is rejected before appending history or starting a worker,
with guidance to the existing Shared tasks and assignment controls.

The browser exposes assignment controls under Related work, with private drafts,
revision conflicts and exact lost-response retries. Attached HTTP/session clients
and agent actions use the same authority. Only the user can detach a settled
conversation, retaining all previously attributed usage and late corrections.
Stop never deletes files, cleans resources or claims to undo external effects.

Finished member participation can now be closed by the assignment owner through
`assignment.retire`. The current-member ceiling excludes retired, detached and
never-created entries while their cumulative usage and membership evidence stay
retained. Retirement requires settled work and completed local/shared tasks,
with native ownership held through the commit. It does not archive or stop a
chat. Existing adoption can readmit the same retired chat after a fresh review,
capacity check and human instruction; the new membership boundary fences old
requests and automatic returns. See [member closeout and reuse](assignment-adoption.md#finishing-and-reusing-a-member-chat).

Qualification includes failure injection before commit and after acknowledgment
loss, preparation/Stop races, concurrent cross-root reservations, restart,
late-result suppression and native finite/resumed/persistent/nested child mounts.
All 478 focused installed-package checks pass, covering adjacent collaboration,
schedules, client actions and accounting; 149 final source checks also pass.
The frontend suite passes 681 checks. Browser checks cover saved limits, private edits, stale saves,
reload, lost responses, narrow layouts and preservation of unrelated work. The
existing two-peer/two-round collaboration browser journey also passes.

A real-provider run exposed loss of assignment metadata during native display
reconstruction; the retained accounting projection now preserves it. A later
run reached its fixture token limit and correctly refused calls in both helpers.
With a higher fixture allowance, the two-round real-provider journey passes:
one agent creates two real peer workers, the host records all three roots' usage,
and assignment Stop interrupts both helpers while an independent real worker
continues and completes. A synthetic local waiting tool makes the Stop timing
observable; provider responses and worker processes are real. These checks do
not establish general parallel-agent efficacy, release publication or host adoption.

The broad earlier-candidate suite reported 6,892 passes, 219 skips and 29 failures.
Five assignment transaction regressions were corrected and pass in the final
focused checks. Twenty-three failures reproduce on unchanged main in the host
environment. One artifact-cleanup timing assertion passes in isolated reruns on
both candidates but remains recorded; the broad suite is not claimed green.

## Supported file-tool source checks — 2026-10-14

Unified now wraps the mounted reference filesystem tools and both reference
patch engines at its existing app-access boundary. Existing files require a
successful read in the invoking coordinator; writes compare the observed byte
hash under short cross-process file locks. Successful edits advance that
coordinator's observation. Independent roots, fresh/resumed children and
remounted workers do not borrow another worker's reads. No whole-project lease,
new history store, automatic merge or extra agent action is introduced.

Creation and patch move destinations require absence. Function patches touch
each resolved path once and preflight all source versions and permissions before
calling the original engine. Stale preflight changes no files. Later engine/OS
failure can still partially apply a multi-file patch: affected observations are
invalidated and the result tells the caller to inspect before retrying. Locks
release on cancellation, and concurrently queued calls cannot silently inherit
the first call's new version. Memory holds at most 512 observations; 4,096 lock
stripes bound lock-file storage without unsafe deletion of occupied locks.

Mounting the actual optional modules also exposed 22 existing policy-composition
failures on unchanged main. Generic settings merging had erased the distinction
between shared extra folders and an explicit narrower bundle allowlist. Relative
patch denials could also resolve against the host's working directory. Policy
composition now preserves that distinction and normalizes paths at the actual
execution folder. All 78 mounted policy configurations pass, including explicit
read-only/narrow declarations. Image policy remains covered by adjacent native
checks; image generation itself is outside the source-check adapter.

The integrated candidate includes main's interim-reply and release fixes.
Qualification against its installed wheel passes 397 native-runtime/file/policy/
provider-adapter checks and four subtests, with the real filesystem module and
both patch engines loaded. Separate OS processes race full writes against full
writes and both patch/string-edit paths, including hard-link aliases. A separate
creation race reproduced a lost update through Unicode-equivalent filenames on
macOS. Normalizing only the lock identity fixes it without renaming either input;
same-name, case-alias and Unicode-alias creation races now admit one winner.
Native
Core/Foundation tests cover finite, resumed, persistent and nested child mounts.
All 681 frontend tests pass. Source/package byte verification includes application
Python and shipped frontend assets.

The full local integrated run completed with 6,975 passes, six passing subtests,
140 skips and 25 failures. Twenty-four failures reproduce on unchanged main
`a513a177`, including an artifact-cleanup assertion that passes alone but fails
on repeated service lifecycles. The remaining assertion expected a relative
image read path; it now checks the intentionally normalized execution path and
passes. The final Unicode correction and affected assertion are included in the
397 installed-wheel checks above. This is not a green broad-suite claim.

A three-round Chromium journey through the actual composer, real provider and
fresh worker passes against the integrated wheel: read a file, introduce a
scripted peer edit, reject a stale overwrite, reread and preserve that peer edit,
then use the provider's native patch route. Native history retains the actual
conflict result; desktop/mobile rendering and browser errors were checked. The
peer edit is scripted, not a second concurrently reasoning model. An earlier
browser fixture authenticated only HTTP and missed the live connection; its
timeout is retained as failed harness evidence. The corrected journey uses the
app's origin-scoped signed session cookie and no simulated provider responses.

This protects these mounted tools within one app data home. Arbitrary shell,
external editors, custom tools, image writers, other installations and external
alias changes remain outside the lock protocol. Byte hashes do not detect a
change followed by restoration. Multi-file patch atomicity, broad concurrent
model efficacy, release publication and installed-host adoption are not claimed.

## Active-work awareness and advisory file intent — 2026-10-13

Independent and commissioned roots now receive the same bounded view of active
work in their canonical workspace. Observed native children are labeled
separately. The host reports recorded execution folders, declaration freshness
and overlap without hydrating peer histories, scanning working files, starting
idle workers or copying private drafts. Packets fit within 8 KiB of encoded JSON
and provide explicit pagination. Declaration bodies stay in existing command
receipts; session metadata keeps only a compact reference.

UI and agent routes share revision-aware declaration/read actions. Replacing a
root's declaration requires that root's authority and both current revisions.
Overlaps remain advisory. Paths resolve only during explicit declarations and
must remain within known task folders; declarations never grant filesystem
access. Missing retained bodies are unknown rather than empty. Exact retries,
including an acknowledgment lost after commit, retain the original outcome.
The browser preserves unsent text and retry identity through navigation, stale
saves and reload, with the optional form under progressive disclosure.

Ordinary `app_control` state readback excludes private composer text, unfinished
message edits and pending attachments, including parent/root pointers, resident
and dormant session aliases, unrelated dispatch readback and rendered browser
observations. Device observations require the selected caller client and its
reported conversation to match. Only a user-requested, exact `/view/draft` or
`/view/messageEdit` read with an explicit `clientId` displaying the calling chat
opts into that editor; it does not expose untargeted clients or another chat's input. The latter editor must also belong to the calling chat. Browser report
text, controls, labels and selected ranges exclude private subtrees, including
the local state debugger. Public controls and selection remain inspectable.

This is a model projection over the existing private UI owner; it does not move
all draft storage to the browser, introduce a separate approval system, or change
the established nonsecret Settings/feedback editor contracts. Explicit draft
mutations such as `canvas.reference` retain their targeted results and revision
checks. Focused service/directory tests cover client selection, pointer aliases,
lazy owner preservation and positive explicit reads. The production browser
journey verifies served asset bytes, independent same-chat drafts, private DOM
and selection exclusion and reload preservation using a synthetic runtime;
it does not claim real-provider or deployed acceptance.

A real native-loop probe exposed accumulation of hook observations within one
turn despite their ephemeral designation. Dynamic coordination references now
use the existing fitted provider-delivery boundary. Both native reminder modes
receive one fresh observation per request; the old observation is absent and
neither enters saved history. Provider budget checks and normal/streamed
transport share the same fitted observation. Host read failure reports unknown
coverage, and auxiliary calls do not request workspace context.

Local qualification: 290 installed-package Python checks pass outside the source
checkout, including both native reminder modes, with two optional loop-module
skips. Another 77 child authority/file/image policy checks and 38 image/guidance
checks pass using the selected native runtime, covering those optional paths.
The combined source/provider-delivery set has 242 passes and three optional
runtime skips, covered by the native runs. All 668
frontend tests pass, the production frontend builds, and the new browser journey
passes private drafts, overlap, explicit reads, navigation, stale revision,
reload, lost-response retry, pagination, mobile layout, clearing and idle
transition. The existing two-peer/two-round coordination browser journey also
passes; its no-paperwork assertion now excludes the optional collapsed file
declaration form. Browser activity is scripted and sends no model input. The candidate
wheel's 290 application/worktree Python files and 311 static files match source
bytes. Selected image evidence remains after general workspace observations;
adjacent image comparison/selection regressions cover this ordering.

Two bounded two-round checks through a fresh real-provider worker and the
installed candidate pass. The worker identifies an independent peer from ambient
context, reads its declaration, declares the same file and verifies overlap.
After the host marks that synthetic peer idle, it observes the new active set
and clears only its own declaration. Both idle-to-idle and immediate ordinary
follow-up delivery pass; file bytes and the peer's declaration remain unchanged.
Only the source worker calls the real provider. Peer activity is a scripted host
observation, not a second concurrently working model.

The first immediate-follow-up probe omitted the API's explicit ordinary-message
handoff flag and requested generation-bound steering. That input was retained
rather than moved to a new turn; the probe timed out without a runtime error.
The browser uses `allowNextTurn` with the observed generation, and that exact
semantics passes the corrected check. The failed probe is retained as evidence,
not treated as a product regression or a successful two-round run.

This awareness slice is advisory. Later slices add supported file-tool source
checks and explicit assignment admission without turning workspace proximity
into peer-control authority. General resource provisioning and parallel-agent
efficacy remain separate gates.

## Explicit repository selection — 2026-10-13

Tasks can inspect, create or attach working copies for individual repositories
under a non-Git workspace using `repositoryPath`. Selection resolves within the
task's canonical/execution folders or a ready associated checkout. Inspections
return the absolute root, and the browser carries that root and its exact source
revision into creation. Each existing worktree record retains its separate Git
identity and source. Selection never relocates the conversation or includes
dirty files unless `carry_dirty` was explicitly chosen.

The affected control class now enforces root authority for all five worktree
mutations, including handoff and reconciliation. A native child cannot borrow
the enclosing root's placement controls. Attach retries also reject a changed
source binding. Existing handoff, preservation and revision checks remain.
Creation and attachment also serialize command identities across repository
locks, so simultaneous reuse cannot overwrite another repository's ownership
record. Two independent managers exercise both races against shared storage.

Local evidence covers two real repositories under a non-Git folder, clean and
explicit dirty copies, stale source refusal, wrong-repository attachment,
symlink/out-of-scope refusal, five child-control denials and explicit command/
import paths that keep using the copied dependency after the canonical source
changes. Both the new multi-repository browser journey and the existing
create/handoff/restart/reconcile/cleanup journey pass. Their runtime is scripted;
this is not qualification of automatic runtime dependency rebinding or preview
provisioning. Those broader binding and integration gates remain below.

A two-round real-provider check through a fresh worker and the installed
candidate wheel creates both clean copies, preserves an uncommitted original,
then inspects both retained records without creating extra copies or changing
execution placement. The final package's 285 application/worktree Python files
match the candidate source. All 41 selected-repository, existing worktree and
reservation checks pass against that wheel outside the source checkout.
This qualifies explicit selection through the
actual agent tool, not automatic dependency mapping or concurrent model efficacy.

The later multi-repository acceptance journey exposed a gap in that evidence:
creation under a non-Git home and handoff from a Git home had passed separately,
but moving the existing non-Git-home conversation failed before runtime release.
Handoff now retains exact home/associated-checkout bindings in its receipt and
checks a destination against its recorded repository, rather than treating the
canonical history home as a repository. Returning home checks the exact existing
canonical directory. Reconciliation uses the retained binding; older receipts can
resolve only an exact home, an associated ready checkout, or a source proven by
the task's creation receipt. Host, root authority, execution revisions, runtime
release and uncertain-effect fences still apply.

Commissioned roots also retain their exact inherited source: the captured folder
must match the task's durable creation receipt and creator identity. That proof
permits leaving the folder and recovering it after an uncertain move, including
after restart or a nested commission. It does not associate the creator's checkout
with the new root or authorize another peer-owned destination. When the source has
a managed Git record, recovery also verifies its retained repository identity.

Focused source tests cover both UI and agent moves between two child repositories
and home, staged/unstaged/untracked preservation, unknown source/target recovery,
restart and retry, substituted-repository refusal, and rejection of an unassociated
legacy destination or replaced home path before release. These are source tests
with controlled runtime release; packaged and browser acceptance remain separate.

## Cooperative resource reservations — 2026-10-13

The existing workspace resource owner now provides explicit reservations in
addition to advisory inventory. Exact keys share atomic admission across all
workspace registrations in one app data home. Exclusive claims conflict;
shared claims require each holder to opt in. Authenticated root/child owners
are distinct, with a combined per-root bound. User reconciliation uses the
same revision-aware actions available to an owning agent. Uncertain claims
remain held, and nothing expires or tears down infrastructure automatically.

An indexed SQLite ledger under `workspace-resources` retains claims and exact
command outcomes. Claim and receipt commit together. Reads page current claims
without loading released history or waking conversations. Old acquisition
receipts cannot resurrect released resources. Workspace Details exposes these
actions under Resource reservations; draft text remains client-local and stale
updates retain it. `coordination.context` includes a bounded current page.

Local evidence: 323 focused Python checks pass, including actual concurrent
process acquisition, rollback on receipt failure, old retries, nested owner
boundaries and native child mounting. All 668 frontend tests pass. Nine browser
scenarios exercise exclusive conflicts, uncertainty, stale revisions, explicit
reconciliation, another owner's acquisition, reload, mobile layout and retained
private drafts. The browser uses a scripted runtime and sends no model input.
Seven unrelated workspace-starter tests fail identically on the pre-change
macOS baseline because of its existing directory-descriptor Git handling;
they are excluded from the focused pass count.

The full local Python run at the initial packaged reservation revision completed
with 6,798 passes, six passing subtests, 209 skips and 28 failures. All 28 failed
cases reproduce on unchanged main `0dcc211` in the same environment. They include
macOS path/case/installer behavior, an absent optional loop module, existing
storage tests and a workflow-source policy assertion. This is not a green full
suite; subsequent compact-receipt changes have separate focused and installed
package checks, plus the live journeys below.

All 16 reservation checks also pass against an installed wheel outside the
source checkout. A two-round real-provider run using fresh worker processes
confirmed a conflict against an independent owner's claim, preserved that
claim, and then acquired, marked uncertain and released the resource after
the owner explicitly relinquished it. Agent replies carry compact domain
receipts instead of repeating unrelated application state.

An earlier live attempt failed at its second prompt. A repeat with captured
runtime errors identified the actual cause: full app-state replies made required
context exceed the local input budget. The pre-fix package reproduces that error;
the compact-reply package passes the same immediate-follow-up journey as well as
the idle-to-idle journey. Both failed runs are retained as regression evidence.
These are bounded protocol checks, not qualification of every steering race or
general multi-agent efficacy.

This completes cooperative reservation admission, not managed provisioning or
an OS resource lock. Different aliases and separate app installations remain
outside its domain. Explicit repository selection, owner-observed preview/process
records, loaded-source provenance, supported file-tool checks and assignment
admission have their own implemented slices. Automatic environment provisioning
and semantic integration remain separate gates.

## Coherent commissioned-task operation — 2026-10-13

New `coordination.create` operations retain one public handle and a private
captured configuration. The host reserves the requester's return capacity before
creating a root and commits the initial request, saved wait and completed
creation receipt together before execution can be admitted. Explicitly independent
work can use `returnNotification:false`. Result inspection through the creation
handle includes its exact initial request, without caller-generated subordinate IDs.

Restart resumes committed plans and reconciles root creation only through its
existing committed command receipt. It can then finish the known-unperformed
initial-input step. A missing acknowledgment leaves root effects unknown and
retained. Lost acknowledgment after input commit keeps the committed outcome.
Recorded Stop, changed assignment or changed execution placement suppresses
remaining preparation. Root identity and captured configuration are checked
before the initial brief. These are local preparation guarantees, not an
exactly-once promise for arbitrary tool effects or external systems.

The private preparation record contains configuration rather than transcripts,
uses existing provider credential references, and is removed when input commits.
Public receipts and ambient context never include that private record. Legacy
operations remain read-only evidence and are not retroactively made resumable.
Automatic preparation and refused/uncertain admission notices are host observations; they do not claim
an uncreated peer produced a report, satisfy a dependency, or authorize replay.

Local validation: 283 focused coordination, creation, recovery and inheritance
tests pass. All 19 new operation/recovery checks also pass against the built
wheel imported outside the source checkout. Three actual Core guidance probes
pass. The production-service Chromium journey completes two rounds for two
peers, using automatic first-request returns and creation-handle result reads;
its runtime and terminal evidence remain scripted. Wheel and source distribution
builds pass.

Two bounded real-provider scenarios were then run twice using actual worker
processes and installed candidate wheels: a successful helper result and a
declined request. Each run created exactly one helper, committed creation,
returned automatically without a manual subscription and preserved the correct
dependency outcome. The final package's 304 installed Python files matched its
source revision. These checks qualify the narrow creation/return protocol;
they do not establish general task efficacy, shared-host adoption, publication,
or the broader multi-client assignment experience.

## Initial repair tranche — 2026-10-13

This section records the initial checkpoint. The later grants, reply-time
artifact retention, repository, assignment and inbox slices supersede its
then-current limitations without changing the evidence recorded here.

The implementation program starts with reproduced defects and established
coordination mechanisms. It does not make the broader assignment/resource
architecture a release prerequisite. The baseline is `be1f1d3`; nine isolated
review probes reproduced the reported defects without touching live services.

| Owner | Implemented behavior | Evidence boundary |
| --- | --- | --- |
| Worker and native children | Each mounted child receives its own app adapter and transport principal, including finite/custom loops, persistent children, resumes and nested delegation. Nested children retain the assigning input context. Root-only controls reject a child principal. | Real Core/Foundation mounting with a deterministic orchestrator; no provider call |
| Host file policy | Filesystem/patch child allow lists intersect across parent, spawn, overlay and retained plans; denies accumulate. Explicit read-only declarations stay read-only. Repository paths follow the execution folder; unrelated external paths remain. Image boundaries remain covered. | Composition and host-policy regressions; no claim of shell or OS sandbox enforcement |
| Shared dispatch | Agent permission writes require an explicit existing scope. Root agents can still perform scoped configuration actions; settings are not made categorically human-only. | Direct-dispatch and app-bridge rejection tests; this is not a general capability-grant system |
| Collaboration | Sealed negative reports and host-observed failed generations notify the exact waiter once without satisfying dependencies. Queue admission reserves space for saved replies. At this historical baseline, a destination had 64 pending/reserved slots and an ordered sender/recipient pair admitted 32 messages per minute; the optional policies recorded below supersede those defaults. Retries retain their identities. | Deterministic admission, failure, duplicate, reservation and Stop tests |
| Creation and worktrees | A commissioned peer uses the creator's actual execution folder and keeps canonical history grouping. A failed read-only handoff preflight or initial foreign-writer refusal is rejected without fencing the source; uncertainty after release still fences it. Previously unknown receipts retain their fence until explicit reconciliation. | Temporary Git repositories, real shared-writer ownership and restart/failure injection; no shared host changes |
| Canvas and related work | Referenced bytes are hashed before presentation in the recorded producer execution folder. A changed or unavailable source is explicit. A verified preview retains its bytes independently of the source. User-created peers are visible alongside commissioned peers. | Actual service/Chromium journey with scripted runtime; no real-provider evidence |

The changed writer class was inspected across filesystem, patch and image
configuration, prepared plans, retained child plans and mounted app controls.
Arbitrary shell programs, third-party tools, and other agent engines remain
outside these configuration guarantees. Older artifact references without a
recorded execution folder remain inspectable as receipts but cannot silently
open guessed source bytes. Files are not automatically archived at reply time;
if they change before first opening, the exact-result view reports a mismatch.

The coherent-creation candidate also integrates the existing bridge-cleanup
commit from PR #616, preserving its original history. Diagnostics remain
best-effort inside cleanup, pending futures are cancelled and bridge effects
are never replayed. The combined candidate passes 66 focused transport, worker
authority and creation checks, including the real 60-second wait. All generated
web assets must be rebuilt when combined frontend source changes.

Local candidate validation: 797 Python tests and two subtests passed across the
affected coordination, runtime, storage, settings, worktree and Canvas families;
638 frontend tests passed. The production-service Chromium journey passed two
rounds with two commissioned peers, reconnect and exact artifact inspection,
including rejection after changing a reported source file while retaining the
existing view and draft. The native child tests used real Core/Foundation with
scripted orchestration. None of these checks claims provider efficacy, publication,
installed-worker adoption, or live-host acceptance.

A subsequent native-loop re-preparation probe reproduced reassignment of a
nested child's context to an unrelated later root input. Keeping the
coordinator's existing bridge and child installer fixes that path; 104 focused
identity, worker, surface-context and file-policy checks pass. The broad browser
gate also needed two fixture corrections: an attachment selector matched both
Open and Remove, and a timer measurement could count a delayed host render.
The corrected timer probe passes repeated runs and detects a deliberately
introduced parent render on every tick. Production rendering behavior is unchanged.

The initial follow-ups for explicit repository selection, commissioned-assignment
budgets/Stop, scoped configuration grants and expiry/fair admission are now
implemented above. Independent roots retain their separate authority. Full
combined multi-round, multi-repository and multi-client qualification remains
distinct from these initial installed-package and provider checks.

## Reply revisions, watches and fair inbox admission — 2026-10-14

A recipient can explicitly correlate a later current root generation with its
original delivered request using the observed `expectedResponseRevision`. The
workspace, task, delivery and Stop boundary must still match. Each reply has a
monotonic revision and stable operation identity; earlier full declarations and
terminal evidence remain in existing reply command receipts. Questions, deferrals
and conflicts remain open reports. The same current generation may correct a staged
report with the observed revision before finishing its turn. The earlier declaration
and retained file versions remain inspectable as superseded; only the latest report
can qualify when that generation ends. Another generation cannot replace an
unsettled report. The correction and superseded marker commit atomically.
Exact retries return their retained revision; new payloads cannot reuse the same command ID.

Negative reports notify immediately as unqualified claims. Successful dependency
acceptance still needs the exact checkpointed native terminal row, root,
generation, delivered input and digest. Unrelated background jobs no longer
invalidate a proven manager result. Missing anchors, unrelated inputs and
background-only dispositions never qualify. Rejected/unknown terminal evidence
notifies without becoming success. A later meaningful report suppresses an
unsubmitted earlier notification, including a question, deferral or failure;
both reports and their notification receipts remain inspectable. A merely staged
success does not replace an earlier notification until its terminal evidence
arrives. Already accepted or uncertain inputs are never rewritten or replayed. Retiring
a queued return releases its inbox slot for a newer backpressured outcome without
waiting for an unrelated event. Reading a result remains passive; it does not
mean that the model reviewed it or retire a same-revision notification.
New subscriptions claim at most one continuation per meaningful revision, capped
at 32 notifications. Old single-notification waits retain their original policy.

One-shot `coordination.watch` actions observe a named root's status or idle
boundary with explicit expiry and cancellation. They do not load its transcript,
warm it or start a turn. Pending watches recover after restart, and claimed
notifications never replay. Their returns use the ordinary guarded inbox and
cannot satisfy a dependency. The actions are available through the agent bridge,
generic shared dispatcher and per-session HTTP API used by terminal clients.

Inbox admission retains the existing 64-slot destination bound, including saved
reply/watch slots, and adds 16 pending inputs per sender and 32 per assignment.
Pending senders rotate using durable admission order. Accepted substantive input
remains retained when a caller-selected deadline expires. The default and maximum
queue duration is one day; callers can choose a shorter deadline. Causal depth
remains recorded from host-retained input provenance, independently of command
IDs or wording, but does not stop authorized work after an arbitrary number of
handoffs. Only actual new human input resets the recorded depth. Existing pair
message rates, inbox capacity, assignment/task limits and Stop still apply. On
restart, current-protocol versioned waits bearing the exact retired hop-limit
diagnostic are reconsidered through normal outcome and admission checks. Stops,
changed objectives, already admitted or uncertain inputs and the separate
32-revision limit are not reopened or replayed.
Publication failure tests distinguish a rolled-back notification from a committed
claim with a lost acknowledgement. Neither loses the underlying report or creates
an extra model turn on retry.

Display attribution checks the exact retained per-revision system claim,
including negative reports, watches, preparation failures and older successful
revisions. An Amplifier update label identifies the author; it does not elevate
that message to human authority or prove current dependency success.

Validation covers service, exact retry/fingerprint, Stop/revision/scope, failure,
backpressure, restart, passive observation and native lifecycle cases. The actual
Core/Foundation/live-loop journey uses scripted provider output with real tool
bridge stamping, delivered inputs, native history and terminal checkpoints. It
proves a question followed by a later-generation result for the same request and
two distinct notifications. It is not a real-provider efficacy or process-isolation
claim. Source, installed-package and final delta receipts are retained separately.
No browser rendering code, release or shared running host was changed by this slice.
Safe tool-boundary informational delivery remains adapter-specific; notify remains
passive and unsupported steering never becomes cancel-and-resend.

## Remaining program gates

The bounded source mechanisms for the authorized October 13 program and the
October 14 follow-up are implemented. Combined qualification is recorded
separately from each earlier slice. Source, a locally installed candidate,
publication and fresh-worker adoption are distinct observations.

Publication and installed-host adoption remain a separate operational step under
the release policy. No shared running service has been replaced by this work.
A later rollout must identify its exact package and worker revisions and retain
post-publication qualification separately; merging these changes does not
establish that an existing user instance is running them.

[Optional assignment work items](#optional-assignment-work-items--2026-10-14)
now provide a shared task list, dependencies and atomic exclusive claims in the
canonical assignment record. Claims assign responsibility; they do not lock
files, automatically start dependent work or verify reported results. Existing
independent chats do not become assignment members automatically. Restart retains
claims; stale responsibility after Stop or detachment needs explicit review.

Universal OS confinement, cross-host filesystem replication and cross-engine
history continuation remain outside the default scope.
Request-only control inside an independent mixed conversation and safe tool-boundary
notification remain adapter limitations; unsupported adapters preserve unrelated
work rather than claiming those capabilities.

Task-owned storage provides directories and local SQLite files, not browser
engine confinement or database-server provisioning. Resource retirement and
checkout archive preserve data without reclaiming space. Explicit dependency
origins and commands replace guesses about application environments. Validation
can prove an exact native command outcome and source observation; it cannot
prove the chosen test is sufficient or that a reported publication occurred.

## Historical collaborative-workspaces candidate — October 6

The following is the earlier candidate's evidence, not the October 14 source
inventory or final program verdict. At that checkpoint the approved same-host
cooperation loop was **qualified for PR review**. Its adapters supported typed reply
declarations sealed against exact checkpointed native terminal evidence,
generation-anchored steering and one stable-ID saved dependency continuation.
Host qualification establishes an attributable agent claim plus observed terminal
proof, not independent artifact correctness. Multiple delivered requests in one
generation can legitimately share that terminal reference; they are not distinct
model answers. Unsealed staged declarations become unknown on restart, never
completed from retained text or anchors.
See [Task coordination implementation and evidence](../TASK-COORDINATION.md)
for exact revisions, earlier failures and acceptance limits. Passing deterministic
surface checks does not ratify replacement execution clauses or qualify later
code from an earlier model run.
Shared-folder operation was cooperative; the later supported file-tool checks
do not turn it into a universal filesystem fence.
Actual-model created-task/adjacent-round/steer checks and Fable delta review
are recorded in the [final PR qualification](../TASK-COORDINATION.md#pr-qualification--2026-10-06),
with the exact revisions, optional skips and pre-existing smoke-test limit.
Durable pending grant proposals retain their exact
source and scope after the transient approval wait/restart and can receive a later
human decision without new chat text. Creation and saved waits fail before
effects unless queue and idle-start authority can admit the required turns.
Existing execution contracts and their decision/candidate evidence are unchanged.

Pre-integration bounded checks on `3b4e188dd03c88f88067d1928c06411ae0625538`:
5,041 Python passes, 111 explicit optional/integration skips, zero failures,
four subtests; all five groups used that same SHA with every collected node
accounted for. The final 265 focused and 440 frontend checks pass, the complete
deterministic browser loop renders the exact terminal, and native scripted/wheel
checks pass. All 95 rebuilt assets match the committed manifest. Exact commands,
skip scopes and the then-pending parent-owned actual-model/Fable gates are in
[the Fable continuation evidence](../TASK-COORDINATION.md#fable-findings-continuation-bounded-fixes-checked-parent-gates-pending).
Earlier failures below and in the linked historical sections remain evidence;
they are not the current source's verdict.

Integrated v0.20.51 source `8003efaf` passed 5,081 unique Python checks with
111 explicit skips and four subtests; 440 frontend checks, the coordination
browser flow and the startup browser checks passed. Rebuilt assets in
`c9c5e3af` reproduce exactly. The additional new-chat smoke's obsolete pre-chat
toggle assertion fails identically on pristine upstream `bbc07023`; its later
assertions are not claimed checked. This qualification does not publish the
replacement execution contract or remove the documented shared-folder limits.

The accepted vision is the target, not a claim that every execution and integration promise is already implemented. This change implements placement, native-history addressing, and the simplified navigation and draft journey. Existing runtime, worktree and handoff mechanisms remain in use.

## Historical placement implementation — September 22

- Name-only placement under a per-host/account configurable default root; per-workspace parent override under More options.
- Explicit existing-folder attachment and automatic native-history discovery, with files left in place.
- Canonical location/name collision checks, pinned directory descriptors during allocation, durable plans and receipts, duplicate command and lost-ack retry handling, and explicit interrupted outcomes.
- Global New chat workspace picker, draft-preserving create/attach/cancel, workspace-local New chat, existing model/bundle/attachment controls.
- Pinned, Workspaces and Recent sidebar; empty available folders included, duplicate names qualified, optional visible paths; existing All chats tools retained.
- Native public IDs for newly discovered sessions, project-scoped native-ID actions, legacy-ID aliases and native SessionRef in the history API. Existing presentation and voice records are preserved.
- Shared UI/agent actions for the complete placement flow and setting.

The legacy explicit-path create action remains compatible for existing clients. It is not the name-only allocation path. Native-history catalogs are still derived indexes; this change does not migrate or delete legacy app data.

## Historical placement evidence

`tests/test_workspace_experience.py` covers naming, root changes, explicit attachment, existing bytes, symlink substitution, concurrent collision, lost acknowledgment, interrupted allocation, private draft attachments/model/bundle, native ID discovery/read, ambiguous IDs and empty-workspace listing. Existing creation, managed-chat, native-history, ownership, worktree, handoff and agent-control suites provide compatibility coverage.

`tests/test_workspace_placement_recovery.py` exits a subprocess immediately after the created placement receipt is durable and before app registration or its command receipt. On restart it checks changed directories, missing paths, leaf and parent symlinks with both the original and a fresh command ID. Refused recovery preserves registrations and placement receipt bytes/timestamps. An unchanged allocation registers once; an already committed app command returns its saved result without repeating placement.

`frontend/tests/workspace-experience-browser.mjs` uses the actual host, production assets and isolated files with a synthetic runtime. It exercises create, attach, cancel, collision/open, settings, a first send with retained attachment, and 320/390-pixel light/dark layouts. `new-chat-browser.mjs` retains coverage for model/bundle choice, reload, first-message delivery, voice/send/stop controls, history details/export, Canvas and client isolation. `new-chat-recovery-browser.mjs` checks failed first-send recovery and unsent inputs. Browser evidence does not claim real provider or deployed Spark acceptance.

Rendered design references under `mockups/` remain illustrative. They are not deployment evidence or screenshots of implemented future features.

Validation recorded on 2026-09-22:

- Full Python run after integration with main `1b56d652`: 3,183 passed, 130 skipped (environment/integration prerequisites).
- After the directory-identity and malformed-reference guards: 33 placement, agent-control and history tests passed.
- After the allocation-recovery correction: 81 workspace, placement, creation and managed-chat tests passed, including 10 subprocess crash/recovery cases.
- Frontend unit suite: 296 passed, including sidebar grouping, pagination, direct pin reordering in both navigation views, hidden-pin preservation, duplicate requests and rejected-receipt retry.
- Production frontend build passed on the integrated source.
- Workspace browser: 13 scenarios, including desktop, 320/390-pixel layouts, light/dark, draft/attachment preservation, real drag-and-drop and Alt+Up/Down pin reordering, retained keyboard focus and persisted order after reload.
- Existing new-chat and first-send recovery browser checks passed.

## Earlier release-candidate validation

The following historical qualification was performed against canonical main `69294c9d458951966944e8a3db8d33e6a27f4dae` after 0.20.7 publication. The candidate was held; the separate performance release subsequently used v0.20.8, so these workspace changes are not included in that published version. The reviewed pin-ordering and allocation-recovery corrections remain included. At that earlier checkpoint its package metadata used 0.20.8. The integration refresh preserves the published release metadata and stores the future feature notes in `RELEASE-NOTES.md`.

- Full combined Python suite: 3,199 passed, 130 skipped (environment/integration prerequisites), including the reviewed placement recovery cases.
- All 296 frontend unit tests and all 13 workspace browser scenarios passed.
- Existing navigation, mobile navigation, new-chat, first-send recovery, review-during-send, settings, settings completeness and mobile settings browser checks passed. Navigation now explicitly asserts that discovered chats use their native public ID; folder-browsing coverage follows the new attach journey and checks retained bundle choice.
- Compiled desktop readiness, memory personalization, publishing and portability workflow browser checks passed with their synthetic fixtures.
- The large-library browser check passed with 22,915 sessions, 3,932 workspaces, four browsers and one terminal stream. P95 latencies were 171 ms for health, 212 ms for admission and 192 ms for actions; drafts were retained and no model calls occurred. These are local fixture measurements, not Spark service measurements.
- Locked dependency resolution, reproducible production frontend build, wheel/source build and local distribution verification passed.

The shipping owner retains the immutable release qualification, merge, publication and deployed-host acceptance. These local results do not cover private companion-runtime release gates or live Spark adoption.

## Historical main integration refresh

Refreshed onto `7d139997ce9cf77cddbc6de4622dfdc0158661a8` after the separate v0.20.8 performance release. Source merged without conflicts; generated frontend assets were rebuilt from the combined source. Published v0.20.8 release notes remain unchanged, and pending workspace notes are in `RELEASE-NOTES.md`.

- 146 focused backend tests passed across workspace placement/recovery/creation, managed chats, automatic/native history, history queries and state transport.
- 298 frontend unit tests passed.
- Workspace experience, navigation, mobile navigation, new chat, first-send recovery, settings, draft switching, multi-client synchronization and state-transport browser checks passed against the combined production assets and isolated fixtures.
- This refresh does not establish deployed Spark acceptance.

## Existing mechanisms retained

Managed Git worktrees use `amplifier_worktrees`, with original source manifests, exact revision checks, explicit dirty-copy support and preservation-aware cleanup. `amplifier_web/worktrees.py` keeps history home separate from execution directory and records pending/applied/unknown handoffs. Native session ownership is enforced by the shared Foundation session protocol. None of these actions run merely because a folder is attached.

## Broader workspace contract limits

The workspace vision also describes Files/Directions, attributed versioned
shared directions and richer instruction provenance. Those surfaces are not
completed by explicit repository selection. Retained helper artifacts and local
preview ownership are now available through their named owners, but do not
constitute every proposed Sources/Canvas workspace surface.

Universal protection from arbitrary writers is not implemented or inferred
from native session ownership. Existing terminal/editor edits remain visible
at the chosen folder. Multi-repository environment/integration work is tracked
in the program gates above; cross-host transfer remains the separate portability
implementation's responsibility. Historical placement/browser results do not
close these broader contracts.

## Task resource owner projection — 2026-10-14

`workspace.resources.managed` reads a selected task's local publishing listeners
and native Operations summaries through their canonical owners, using indexed,
bounded pages. The workspace Details resource surface exposes the same action.
Local previews report the actual bound port, immutable release directory and
owner-observed liveness; Operations reports retained lifecycle evidence without
re-probing process IDs or exposing commands/output. Stopped and unknown outcomes
remain visible. Existing reservations are presented alongside these observations
without inventing a claim-to-process mapping or automatic cleanup.

The projection is read-only. The owned-storage actions below add specific local
allocations; arbitrary dynamic preview ports, operation cwd missing from its
owner, remote targets and external processes remain explicit limits.

## Workspace-local task storage correction — 2026-10-14

New ordinary task storage follows the chat's actual execution folder and existing
file-tool policy, so routine workspace writes need no configuration approval.
The private resource ledger retains original placement and root identity. An agent
must have observed access for newly created paths; existing unchanged parents
and ignore files are identity-checked without requiring broader write access.
Explicit denials and narrower policies remain enforced. Direct user allocation never starts a worker. A local
ignore file excludes storage from ordinary Git/DTU source snapshots. Managed
checkout archive/restore follows verified worktree move records and retains bytes.
Existing external allocations and retries keep their original placement; no
permissions, pending requests or retained files are silently migrated. The DTU
backend's own state remains app-private. This supersedes the outside-repository
placement described in the original slice below.

Focused acceptance: 129 backend tests, six client tests and 15 browser scenarios
passed in an isolated qualifier. Actual Core/Foundation root, fresh-child and
resumed-child writes exercised both patch engines without approval, including
denied, narrower and read-only policy cases. Browser checks used real allocation
and receipts with a scripted conversation runtime; no provider call was made.
Live adoption by previously running workers and old pending approvals remain
separate boundaries.

## Owned task storage — 2026-10-14

The existing workspace-resource ledger now owns explicit scratch directories,
browser-profile directories and initialized local SQLite files. The shared
`workspace.resources.provision/read/retire` actions serve the browser, an
authenticated session HTTP client and the actual native root `app_control` tool.
Allocation paths are private app-owned locations outside source repositories.
No caller-supplied destination, background browser launch or database server is
part of this implementation.

The owner saves intent before effects, serializes the physical allocation,
records directory/file identities, bounds outstanding allocations and pages
reads. Exact retries retain the same identity and files. Stop, generation and
placement changes before admission suppress effects; a canceled wait lets an
already-admitted filesystem owner finish. Missing/replaced paths never trigger
recreation. A departed allocating owner becomes unknown after its lock is free.
Retirement preserves all contents, including unknown effects, and releases only
the allocation-count slot. There is no deletion action, byte quota or automatic
garbage collection. Native children and peer roots cannot borrow ownership.

Allocation never changes file access. A ready worker can passively report its
actual supported reference filesystem/patch policy; absent workers, custom tools
and changed mounts remain unknown. Broad existing access needs no extra approval.
Missing access uses the existing explicit `file_access` capability and scoped
permissions route. The observation does not certify shell/browser/database policy
or bypass current file-tool hooks. A profile directory is not an isolated running
browser; a SQLite file is not a network database.

Local slice evidence: 120 native-runtime tests passed across allocation,
reservations, managed owners, actual mounted tool policies, HTTP binding,
finite/resumed/persistent/nested/recipe-compatible child authority and configuration
grant races. All 699 frontend tests and the production build passed.
An isolated browser fixture passed 15 resource/preview scenarios, including real
storage, lost-response retry, draft reload, peer separation and retained user
files. Its conversation runtime was scripted; this is not provider, publication
or deployed-host acceptance. The parent integration must qualify the combined
source and regenerate its own assets.

## Return recaps and portable work diagnostics — 2026-10-14

`coordination.recap` compares a caller-owned cursor with bounded saved request,
reply, chat and resource-reservation observations. It returns eight changed
items at a time, leaving undisplayed changes unacknowledged. Related work keeps
its cursor per chat in tab-local storage and advances it only after a visible
response is presented. Closing the view, changing chats or hiding the tab fences
late responses. Reading does not alter shared read state, navigation or drafts.

`coordination.diagnostics` exports numeric and enumerated observations from the
same scoped receipts, already loaded native provider/bridge records and existing
process loop-delay samples. Correlation IDs are hashed; message content, paths,
tool payloads and private drafts are excluded. The browser adds bounded numeric
action/network samples from its existing telemetry, without routes or command
names. A local ring also records recap dispatch response delivery to the component
through the React layout effect after DOM mutation. Its exact boundary, monotonic
clock and current-mount scope are exported; it does not measure physical paint
or overall app rendering. Missing samples remain explicitly unavailable;
inclusive, overlapping timings do not establish cause or end-to-end latency.

Both passive actions share UI, session HTTP and agent dispatch, recheck scope
after asynchronous owner reads, and do not hydrate history or start workers.
Version/scope mismatches reset the comparison explicitly. Bounded page gaps are
reported rather than treated as deletion, completion or released resources.
Provider/bridge export coverage uses recorded metadata fixtures; the production
browser journey uses a scripted runtime without model calls. Release, installed
worker and provider acceptance remain the integration owner's separate checks.


## Optional assignment work items — 2026-10-14

`assignment.tasks.list/read/wait/create/update/claim/release/complete/cancel/reassign`
use the canonical assignment record and existing command receipts. A bounded
128-item list supports owner-curated acyclic dependencies and atomic participant
self-claims. Per-item revisions and exact claim identities fence stale holders.
Stop and detachment retain stale responsibility; ordinary restart retains a valid
claim. All recovery is explicit, with no claim expiry, automatic work, replay or
file-lock promise. Reported completion is separate from artifact verification and
integration validation. Completed and cancelled tasks are immutable.

The bundled Shared task list uses these same actions and readable scoped member
and prerequisite names. Open views wait passively on scoped versions; hidden or
closed views cancel their wait. Read paths inspect metadata and command receipts,
not history or assignment usage. Private form/pending request state stays in the
browser tab and uncertain mutations retry the same command ID. Confirmed client
rejections clear only the pending request and refresh the list, retaining drafts.
Older observations cannot replace a newer assignment revision; mutation, closure
and visibility boundaries invalidate outstanding task observations. Completion
drafts retain their original claim identity and require explicit review before
use for a replacement claim. Free-text drafts are excluded from UI observations.
Mutation receipts retain only the changed item, not repeated copies of the board.

Source checks cover concurrent claims, dependencies and cycles, old-holder fencing,
Stop/detachment, restart with explicit later completion, delayed admission, durable
commit failure and lost acknowledgement, shared UI/agent actions, scoped passive
waits, and UI identity/reconnect behavior. Installed package, real-provider and
computer-use acceptance remain separate integration checks.

## Workspace work overview and combined-check controls

Workspace work opens from the main workspace page and the canvas Chat overview,
with no separate conversation-header control. The workspace page disclosure uses
a twenty-row paged chat picker; inspecting a named chat preserves the active
conversation and composer draft, including when browsing another workspace. The
canvas entry reviews the current chat. Initial inspection reads that chat context
and one workspace-scoped page of twenty root summaries. Active work and file-overlap
information still come from the bounded awareness owner; paging is explicit.
The page distinguishes independent active peers, commissioned helpers, attention
and recent request outcomes. Shared tasks/assignment controls, follow-up watches,
recap diagnostics and exact messages/results are disclosures. No inspection
starts work, changes execution placement or assigns an independent chat.

Combined checks now select known working copies or an explicit repository folder,
retain inspected source versions, and choose saved validation runs per repository.
The selected candidate survives source rechecks and provenance inspection; each
run selection stays bound to that exact candidate. Acknowledgement loss preserves
the original invocation identity and arguments across reload. Collection reads
native outcomes without replaying commands. Exact identities, manual JSON/import
expectations and reported publication remain available as advanced evidence.

Focused component checks cover passive bounded reads, stale visits, duplicate
repository names, exact source capture, candidate changes, saved-run paging and
uncertain retries. The production frontend builds successfully. Browser scripts
were updated for the explicit disclosures; actual browser, responsive layout and
provider acceptance remain separate integration-owner checks.


## Chat write settings and inherited defaults

`permissions.get`/`permissions.save` support explicit session scope, and
`permissions.reset` removes only that native session's filesystem allowed/denied
write override keys. Chat → Advanced → Files this chat can edit previews the
inherited workspace values before reset; the separate Project file access form
continues to edit shared project/local settings. Both UI and authorized native
roots use these actions. An agent requires the existing file_access/session grant
for save and reset, including a reset that restores wider access.

Foundation still owns the settings file, overlay precedence and locked mutation.
The preview revision includes inherited configuration, current session settings
and execution identity, and is checked again at the mutation. Exact command
retries do not repeat writes. No-op reset preserves an absent settings file or
existing file bytes. Reset preserves unrelated/read settings and shared files;
it does not clean up earlier workspace-local overrides. The owning runtime
refreshes only this chat through its existing safe-boundary mechanism. Saved
policy previews do not claim actual mounted access, override bundle/parent
ceilings, alter shell policy or start a turn. A pending file-access fence is
saved before the canonical file write. Existing work may settle; new root,
worker and voice inputs cannot bypass it, peers wait, and schedule/observation
guards recheck it. Confirmed retirement or an absent owned mount clears it.


## Guided review before continuing — 2026-10-14

Workspace work now offers a demand-loaded review of saved workspace chats and
next actions before continuing. `coordination.review` uses the indexed history
directory, existing assignment claim rule, and the source chat's integration
receipts. It returns at most twenty root summaries, eight claims needing review,
and four prior source observations. Stable directory keyset cursors stay bound
to the originating chat and workspace. Each page is a fresh observation; no
whole-workspace snapshot or readiness certificate is claimed.

The review distinguishes observed activity from saved status, pending decisions,
settings/file-permission fences, saved placement, bounded unresolved move observations, and
response completion. Its existing directory projection retains claim identity
and interruption generation for passive checks. Older unrefreshed summaries
remain unavailable rather than acquiring a false current claim. The bounded
move projection examines sixteen retained receipts, disclosing older history
when present. Opening the review does not admit dormant peers, hydrate native
history, inspect repositories, query GitHub, warm workers, send input, change
claims or create a new canonical ledger. Existing Open chat, shared assignment,
and integration controls own explicit resolutions. Worker provenance remains a
separate explicit read, and installed app version does not prove worker adoption.

Focused source qualification passes 57 backend checks across review, coordination,
assignment tasks and directory query/preservation, and 43 frontend checks across
review, workspace work and assignment task controls. These cover native tool/UI
read parity, child authority, scope and cursor fencing, passive restart discovery,
canonical stale claims, old unavailable summaries, private-draft preservation,
late replies, bounded page replacement, and explicit-only worker inspection. The
production frontend builds successfully. The integrated acceptance below records
the separately installed package, browser and live-provider evidence.


## Recovery and guided-review integration acceptance — 2026-10-14

The combined candidate `4145b14764073a4fdeb3e2dada8b25dc30240ffe`, based on
main `0925770e`, passed 190 focused backend checks against the built wheel and
114 frontend checks. The package was installed in an owned isolated instance;
all package files matched the wheel, and all eighteen saved chats, transcripts,
drafts, fixture files, configuration and dependency metadata were preserved.
The frontend identity was `9091c62d83762119` (candidate version 0.20.154).
This is candidate qualification, not publication or production adoption.

Computer-use acceptance opened the guided review of eighteen saved roots,
inspected an absent worker without starting it, followed the existing shared-task
and source-check controls, and opened another chat while preserving an unsent
draft. Review-only operations left native history and fixture files unchanged
and started no workers. The review fit 320- and 390-pixel viewports without
horizontal overflow. A real provider used native `app_control` to dispatch the
same `coordination.review` action successfully; its eighteen observations
retained the source, PR, worker-adoption and replay limitations.

Recovery acceptance exercised real provider work across browser disconnect and
reconnect, a durable capability decision across an owned-service restart followed
by Deny, and stopping one chat after a synthetic file effect while an independent
chat completed. Observed effects were not replayed; retained conversations,
existing drafts and configuration were preserved. The installed candidate showed
an older misleading completion summary as Interrupted from its exact retained
root receipt and showed a fresh stopped native turn as Stopped. Ordinary native
approvals now expire after host replacement; focused checks verify that answering
an old card cannot revive its worker. Later successful retries and explicit
terminal outcomes retain precedence over earlier interruption receipts.

Five isolated child-process update fixtures preserved pending replacement state
and history, blocked new work, and avoided synthetic effect replay. Installation
and service operations were stubbed; normal live updater interruption remains
unverified. Browser reconnect did not prove exact command-acknowledgment loss.
The live Stop case observed cancellation rather than a successful native reply
arriving after Stop; that stronger case has source coverage only. Historical
repair requires retained exact generation receipts and does not reconstruct
aged-out outcomes. Production services and saved production chats were unchanged.

## Managed checkout handoff boundary — 2026-10-15

`worktree.handoff` now retains its pending receipt while the existing worker
settles the calling tool and native child work. `handoff_boundary.py` inspects
native generation, input, tool, operation and child state under the worker's
command lock. It yields saved goals at their completed-turn boundary and requires
the existing checkpoint/park path before cooperative writer release. It does not
cancel a tool to make a move possible or acquire a foreign conversation writer.

The originating worker records the already admitted native jobs and exact child
runs before publishing its move request. New delegation is briefly fenced even
when that attempted move is later rejected; known rejection releases the fence.
Preparation admitted before the fence, including nested preparation without a UI
row yet, keeps its existing run identity and may settle. Ordinary tools continue.
The host preserves queued and in-transit child model requests only when their
private run identity matches that receipt. Root observations may include only
completion inputs tied to those captured native jobs. Unknown input provenance,
Stop, changed source/configuration/task authority and model budgets remain gates.

A bridge error carries the exact receipt disposition only after the host handler
finishes: positively absent releases the provisional marker; a committed receipt
retains its actual state; unreadable or lost acknowledgement stays uncertain.
Existing native boundary inspection can bind that same receipt after reply loss.
No second action is issued and no input or operation is replayed. Native result
identity depends on loop-live preserving the existing `Input.call_id` field;
older producers without it cannot qualify late completion observations.

`worktrees.py` persists positive release evidence separately from placement and
recovers that confirmed boundary after host replacement. Earlier preflight/wait
interruptions reject safely; unconfirmed release stays unknown. Active work gets
one host-attributed completion observation through the existing collaboration
inbox, guarded by its original user-message, Stop, task and execution revisions.
Changed authority suppresses continuation. A known placement stays applied even
if notification persistence fails; the retained notification identity is recovered
on restart. Completed idle moves do not hydrate old histories at startup.
Goals that end or exhaust their turn cap while waiting do not get another turn.
The return retains its original transport-bound input lineage and causal depth.
Configuration authorization follows that ancestry through the existing grant
checks; missing lineage stays unverified and an unprivileged peer cannot borrow
the moved conversation's grants. Backpressured returns retain an inbox slot.
A UI move preserves the active host generation's ancestry too; placement consent
alone does not elevate a running peer request or replace missing input lineage.

Focused regression coverage includes pending native tools/children, goal progress,
Stop before and after release, positive-release restart gaps, later placement,
duplicate requests, foreign writer refusal, inherited/multiple-repository
placements, native handoff and collaboration admission. The standalone
`tests/fixtures/handoff_worker_probe.py` additionally runs real worker subprocesses
with a scripted provider: its native tool receives and saves a pending receipt,
the first worker checkpoints and exits, and the continuation runs in a second
worker in the target checkout. Persisted native history contains one original
input and its tool result; history home and an unsent draft remain unchanged.
Its `--goal` mode verifies the saved native goal resumes with its remaining turn
budget; `--goal-cap-one` verifies a move can finish without restarting an exhausted
goal.

The probe uses a minimal snapshot bundle and needs installed loop-live and
context-simple modules. Run it with the qualified runtime interpreter and a new
private output directory; it uses no provider credentials or user history. This
establishes runtime lifecycle with scripted model replies, not real-model
cooperation, browser behavior, packaged-release acceptance or production adoption.


### Ordinary reads and searches

Unified now composes filesystem read limits into the reference grep/glob tools
separately from write policy. Absent or null read limits remain unrestricted;
explicit empty lists deny all, explicit narrower declarations remain ceilings,
and read denials accumulate through fresh and retained child plans. The
standalone search module keeps its working-directory default when configuration
is omitted; Unified supplies explicit null for ordinary unrestricted reads.
Module-level and per-tool search settings cannot erase inherited limits.

Reference-module changes enforce reader denials and select the Python grep
engine whenever explicit denials require per-file checks before opening.
Case/Unicode aliases and hardlinks to denied files are checked by filesystem
identity; unrelated hardlinks do not create allowlist grants. This mode uses
Python regex semantics and may be slower. Restricted searches use tool exclusions
instead of ambient ignore-file contents. Writes and history/draft authority are
unchanged. Arbitrary shell/external tools remain outside these guarantees.

Qualification checks the actual mounted reference modules before input admission,
including the existing no-model update probe. Older recorded modules with missing
semantics require Settings > Updates to check/install component updates; they do
not justify a configuration grant. Fresh qualified generations can adopt updated
sources, while already active work and retained explicit child ceilings remain
preserved. Source tests and a provider-free Core/Foundation mount cover this
behavior; release, installed-worker adoption and the reported provider conversation
remain separate acceptance steps.

## Idle worker retirement preserves peer admission — 2026-10-19

Retiring a settled worker for memory retention or an updated runtime is process
maintenance. It leaves the conversation idle and eligible for a new guarded
peer request, including after host restart. A native `session.closed` event at
that boundary must not mark the conversation Stopped and require a human relay.
The host records the maintenance reason before sending the retirement command;
refusal clears that reason, and an unacknowledged exit remains an explicit error.
A concurrent user Stop takes precedence even after the retirement acknowledgement.

Regression coverage exercises real subprocess transport, AppService persistence,
and exact host admission with a scripted native endpoint: a fresh peer request
after retirement, during retirement, after host restart, and after an explicit
Stop. New input reaches only a fresh process and only once. Existing messages
and peer attribution remain intact. These checks make no real-model or deployed
release claim. No existing stopped/error record is silently reclassified, and
previously suppressed or unknown requests are not replayed.

#### Settled failed members

A fresh human-backed owner request can admit one reconciliation input to a failed commissioned member. The host requires a recorded terminal generation, no active child work, and the existing native ownership lock proving the previous worker is absent. A classified provider cause gains terminal identity only when its observed admitted input and active generation match the failure event; later generic worker errors retain that cause. Ownership stays held through the saved receipt. The failed generation and its effects remain evidence; no input or command is retried. A newer failure, Stop, membership change, human input or pending access change invalidates admission.

The current failed root generation owns its diagnosis even when the last UI turn
is older or the generation includes a native delegate observation. Preparing a
replacement worker preserves that diagnosis until a new generation starts;
worker readiness alone does not recover the failed turn. Historical peer-receipt
requirements and all admission guards remain unchanged.

Explicit provider/tool safety failures block peer resumption even after ordinary Stop changes the displayed status. Passive inspection and a separate permitted human request through normal chat admission remain available; this is not a permanent ban on unrelated work or a mechanism for clearing the halted operation.

Unknown assignment Stop receipts retain their original outcomes. A later durable recovery receipt can settle only its explicitly listed session/Stop pairs, using the native ownership proof held through that recovery commit. Further recovery consumes this historical evidence instead of reacquiring a detached former member's new independent writer. Current bound members must still settle, and any new or unproved unknown Stop still needs native ownership proof.

Saved capacity denials continue to block automatic turns. A fresh host-verified human recovery input can reach the ordinary provider admission guard, which rechecks current conversation and assignment limits before any model call. The input must be newer than the cached denial and belong to the observed active generation. Peer input additionally needs its actual admitted human-resume receipt; a caller-supplied flag or message text is insufficient. Only successful durable host admission clears the worker's old denial; it never resets limits, task progress, usage or receipts. This allows an increased assignment allowance to take effect without a no-op settings edit in every worker, including workers with enabled local budgets. A still-exhausted or uncertain local allowance remains blocked by its current policy.

### Assignment model admission waiting

Temporary concurrent-call saturation and renewable held call allowances wait in a
host-owned FIFO before invocation. Waiting calls have no admitted usage receipt.
A released receipt wakes waiters; each checks the current input, generation, Stop,
configuration, native owner and actual cumulative/assignment policy again. Unknown
outcomes remain held and are never inferred complete to free a slot. Disconnect or
cancellation removes waiting entries; admitted calls finish independently.
When unknown and live calls share the concurrency ceiling, live completions can
still release their own slots, so new calls wait without changing the unknown
receipts. Unknown reservations alone at or above the ceiling remain a hard pause.
Cumulative token/cost and unknown-usage policies still apply at every admission.

Settings → Advanced → Performance & runtime → Model capacity applies across this
host. The default permits 256 waiting calls with no time limit; the user can change
both values. A zero timeout waits until capacity or cancellation. A configured
expiry reports that no provider request was sent and does not save a budget pause.
Policy changes wake existing waits; they neither restart nor replay any work.

Individual cancellation of a worker's waiting model call sends a capacity-only
transport cancellation for that exact bridge ID. The host retains its per-worker
lookup until the worker consumes the reply, so cancellation also covers a reply
in transit. A waiting entry is removed without stopping the worker. If admission
already persisted a matching receipt, cancellation marks that unended receipt
outcome unknown and retains its reservation; it does not infer that a provider
was never invoked or free capacity. Unknown IDs, another worker's frames, and
late cancellation after reply consumption cannot change another call. Unrelated
mutation bridges retain their existing completion behavior.


#### Retained terminal evidence for older failed members

Some older workers saved a generic unknown failure without its generation ID.
Guarded peer recovery may retain separate terminal evidence instead of changing
that failure: the latest root event must be a failed or detached generation,
its input IDs must exactly match the current host-observed terminal generation,
and the generic failure must follow that terminal. This witnesses the stopped
turn, not delivery, success or authority of its old inputs. Native service inputs
need no peer receipt; missing or unknown old receipts stay unchanged. A fresh
owner human request and native stopped-owner proof are still required. Later
input, terminal, Stop or configuration changes invalidate the prepared recovery.
Warming retains the same failure until a new generation actually starts. A saved
explicit safety halt in the current root generation still blocks recovery even
if a later detach or older generic projection hid it. This uses bounded retained
lifecycle records, not transcript scans. It admits one new reconciliation request;
no old input, operation or provider call is replayed and no failure cause is inferred.

## Explicit responsibility for existing chats

[Existing-chat adoption](assignment-adoption.md) adds a settled, independent root
through a revision-bound preview and explicit manager/human instruction. The
canonical assignment retains a distinct adoption receipt, not a fabricated
creation operation. Native ownership, unresolved configuration, task state and
concurrent Stop/input changes are checked before committing membership. Existing
manager continuation and placement controls then use that responsibility. Earlier
history and usage stay attributed as before; adding itself never starts a turn.
The workspace/canvas assignment controls expose the same operation. Independent
chats in a shared folder remain independent unless explicitly added.


## Optional peer inbox counts — 2026-10-30

The earlier 64-slot destination, 16-per-sender and 32-per-assignment bounds above
record historical behavior. They are superseded by optional global Work
concurrency settings, all defaulting to 0 (no count ceiling). Positive configured
limits still reserve return capacity atomically. Sender selection considers the
complete pending set using indexed admission order, then loads one bounded FIFO
batch. Relaxing a limit resumes saved backpressured returns through existing
guards without replaying unknown operations or clearing Stop.

Qualification: 223 focused Python checks passed, both selected new regressions
failed on the unchanged base, and the built settings browser passed finite/zero,
lost-acknowledgment, concurrent-writer, narrow-layout and cold-reopen checks. See
[scope and limitations](../validation/peer-inbox-policy-qualification.md). This
source evidence does not establish release or live-worker adoption.

## Optional message rate and complete watch delivery — 2026-10-30

The earlier fixed pair-message rate and 64-watch target limit are superseded by
optional global Work concurrency settings, both defaulting to 0 (no count limit).
Positive values still reject new requests atomically and preserve exact retries.
Changing a limit does not retry rejected work. Existing saved returns retain their
inbox admission path.

Lifecycle observation visits the complete pre-event watch set using 64-row keyset
pages, with one captured status/time and a row high-water. Later registrations do
not consume an earlier event. Restart reads are also bounded. Stop, cancellation,
unknown delivery and once-only notifications retain their existing guards.
See [qualification and limits](../validation/peer-watch-policy-qualification.md).
