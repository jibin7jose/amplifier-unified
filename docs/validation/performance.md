# Performance validation

This guide applies [WS1/3/5](../../contracts/working-set.v1.md) and
[CS3/4/7](../../contracts/client-state.v1.md) to changes in the current application
and its dependencies. It governs evidence and regression checks, without changing
public read/action contracts or claiming the target architecture is implemented.

## Declare the operation and its scaling inputs

Before changing a frequent read, publication, save or preparation path, name its
canonical owner, trigger and consumers. State what grows: active owners versus
stored sessions, messages and their bytes, retained jobs/receipts, subscribers,
requested pages or pending work. Include content shape where it matters: many
small rows, a few large tool bodies and nested mutable values exercise different
costs. Multiple growing inputs can multiply work, such as scanning every message
for every retained job.

State the intended work bound and any necessary exception. Recovery may need to
validate complete history; that is different from doing the same scan on each
unrelated update. A required linear pass over the admitted owner's transcript is
legitimate. Guard against repeated full scans per job where one transcript pass
plus per-job checks preserves the same recovery result. Trace reads, copies,
serialization, hashing and comparisons before filtering the final result. A small
response, unchanged file or quiet UI does not prove that its construction was cheap.

When a compatible file and immutable resource store the same presentation value,
reuse one canonical encoding. Keep resource identity and transaction/rollback
ownership unchanged. Qualify changed and unchanged saves, exact readback and
borrowed nested edits; skipping writes alone must not conceal duplicate encoding.

## Separate current presentation from historical evidence

Routine state carries the current fields its consumers need. Historical bodies
use existing authorized detail/history reads when they are not needed in that
view. Apply projection before expensive copying or encoding. Review every removed
field against its consumers and retain exact identity, revision and read coverage.
Do not hide a required current field or disable a control merely to meet a byte
target; omitted coverage is distinct from an empty value. If current fields remain
unbounded, report that limitation rather than claiming a total payload bound.

Derived caches and indexes retain one canonical history authority. Document their
dependency/invalidation witness, ownership, growth limit and retirement behavior.
An object identity, list length or file size alone does not prove unchanged nested
content. Count cache-validation work too. Moving a full scan to another process
changes responsiveness, but does not by itself reduce total work or memory.

Saved output records own their immutable bodies. Their list/read/image actions
must not hydrate or copy the conversation transcript to validate output ownership.
An output write retains its location and execution revision across an asynchronous
read, then rechecks that revision, transfer authority and any explicit source
message before committing. The output tests guard against unrelated history
traversal and exercise changes during that read; do not replace these fences with
a full conversation snapshot.

A selected saved-image set shares one off-thread body/hash/PNG conversion pass
(two for chunk-manifest lookup), not a separate event-loop resume for every
image and conversion. Keep SQLite access on its owner thread and recheck the
entire set's current reader relationships after the read, including pretransport
validation. `test_output_image_preparation.py` measures these work bounds and
injects loaded-loop resume delay under the existing image deadline. Temporary
context unavailability suppresses image delivery but does not manufacture a new
input identity or silently discard the selection; a verified input change does.

## Keep a meaningful regression check

Choose a small case and a representative larger case for the affected operation.
Hold the active/viewed set fixed while growing the historical input when testing
independence from history. Exercise cold misses and warm reuse when either changes.
Use synthetic fixtures that preserve the relevant size, shape and event sequence;
an empty history or mocked-away persistence cannot qualify that cost.
Vary independent inputs separately: a fixed notice set in a growing native
catalog exposes different query work than a growing notice set without native
history. Inspect the executed query plan and work counts; selecting a few fields
or joining a small result does not prevent an earlier full materialization.

Also vary retained body bytes with the row count fixed. A constant SQL instruction
count can still hide repeated parsing of large bound JSON parameters. Scalar
queries should not bind unused bodies; measure parameter/statement bytes as well
as rows and preserve the authorized full-detail read.

Prefer stable work assertions at the affected boundary: rows read or visited,
records copied/encoded, queries, publications, frames/bytes, retained cache bytes
or runtime starts. Pick the counts that expose the regression; avoid tests that
only mirror helper calls. A check should detect the old behavior and still permit
a different correct implementation. Pair the work assertion with semantic oracles:
exact history/receipt preservation, current control availability, revision and
subscriber causality, and no unintended wake, replay or provider call as applicable.
Exercise failure, cancellation, concurrent mutation or lost acknowledgement when
the optimization changes those boundaries. Select affected cases rather than
turning this list into an unrelated universal test matrix.

Declare each budget with its workload, units, scope and reason. Derive byte/count
limits from the intended resource contract and measured fixture; derive timing
tolerances from a comparable baseline and observed noise. Do not invent a universal
latency cap or loosen assertions/retries to obtain a pass. Where timing is noisy,
retain the distribution and use deterministic work counts for the regression guard.

## Record evidence that another owner can compare

Compare baseline and candidate with the same fixture, assertions, platform class
and runtime mode. Record exact source, built client, installed package and loaded
dependency identities relevant to the path; files present on disk alone do not
prove which code a process loaded. Record cache state, instrumentation, repetitions,
concurrency and provider/network use. Label source, synthetic installed and live
observations separately, and retain failed setup attempts and material limitations.

Measure the user-visible boundary as well as the changed component where relevant:
admission, acknowledgement, publication and ready/rendered completion can differ.
Do not add nested inclusive timings. Process CPU can include threads; wall time
includes waiting. Distinguish application payload bytes from compressed wire bytes,
RSS from PSS, and cold startup from warm reuse. Account for observer overhead when
it can change the conclusion. Keep diagnostic collection bounded and sensitive
receipts private. Public evidence uses synthetic data, counts and hashes rather
than transcript bodies, credentials or private environment details.

`AMPLIFIER_RUNTIME_TIMING=1` is a startup-only, explicit opt-in for content-free
worker preparation and checkpoint phases in the existing bounded timing ring.
It records context view/factory, surface preparation, provider budget/refit and
checkpoint read/save boundaries. Native provider-hook start/end observations are
separate: correlate only a proven single request window with matching session,
generation and iteration; a short-circuit need not reach the end observer.
Worker phase marks and host receipt marks separate execution from event delivery
only when their `clockDomain` and `hostClockDomain` match. Never subtract wall
clocks or add nested spans. This observes existing behavior, does not change
timeouts or enable request-body recording, and cannot explain earlier unrecorded
work. Activation and any required live worker remount remain an adoption decision.

For disposable query workers, measure delivery as well as worker calculation.
Separate executor handoffs for tiny cleanup operations can repeatedly incur a busy
event loop's scheduling delay. Keep one owned result-consumption/cleanup handoff;
the real-process transport checks bound warm-read handoffs and ensure cancellation
settles file work before releasing spool ownership. Shell query diagnostics retain
fixed failure/disposition categories and `ownerWaitMs` after transport completes.
That wait is included in `readMs`; do not add the two or interpret either as worker
CPU time. These checks do not establish a bound on unrelated host persistence work.

## Keep qualification proportional to risk

Follow the [fast publication policy](release-qualification.md#local-first-fast-publication-policy-updated-november-4-2026).
Run the affected regression and changed user flow; use installed built assets when
the claim depends on packaging or browser/server integration. Reuse exact applicable
receipts rather than repeating unrelated suites. Broad post-release checks remain
background follow-up, and this guide adds no blanket merge or publication gate.

Keep budgets and measurements with their executable fixture or exact evidence
record. Update them when workload or contract changes, with a reason and retained
comparison. Link that evidence from the PR; passing one fixture is not a claim
about all histories, platforms, provider behavior or deployed user latency.

### Cold browser startup and HTTPS delivery

Measure complete critical-asset downloads over actual TLS, including time to first
byte and time to last byte under concurrent host work. A fast health response,
HTTP-only fixture or completed response headers does not prove a usable reload.
Tiny repeated executor reads can multiply a busy event loop's delay across one
asset. Keep transfer buffers bounded and honor slow-client backpressure, rather
than loading arbitrary files into a permanent cache or bypassing authentication.
`tests/test_static_files.py` checks the real HTTPS path's read count as asset size
grows, exact bytes, validators, ranges, disconnects and static-file confinement.
Run these checks when changing file delivery or upgrading its HTTP dependency;
retain aiohttp's file and routing checks when adapting its transfer path.

## Long-lived conversation baseline

`tests/fixtures/session_history_scaling.py` exercises the actual host save/publication
boundary in an isolated instance with one current turn and no provider or subscribers.
It grows completed message bytes, worker records and accounting receipts separately,
retains first/warm timings and a profile, and verifies that saves retain the history
without starting execution. Run it inside an owned DTU, with that checkout's normal
dependencies, using `python tests/fixtures/session_history_scaling.py --output DIR`.
It is a diagnostic fixture, not a passing latency gate or live-host forecast.

Include retained live placements as well as retired accounting: their turn IDs
must resolve and tool observations must use valid canonical identities. Assert
that the expected rows survive durable readback. Otherwise a fixture can silently
filter out its historical tools/model calls and measure a much cheaper save.
The observation case changes a receipt before each of its four timed saves. The
following profiled save leaves execution unchanged; label that profile separately
from the changed-receipt timings. Unchanged-history reuse is
not evidence for that path. `test_accounting_projection_copy.py` also grows plain
historical receipts while checking independent copies and bounded generic
recursive-copy work; custom and nested receipts keep their existing copy behavior.

Use the measured dominant work to choose a change. Current caches may make moderate
unchanged histories cheap while larger bodies fall back to full preparation; measure
both sides rather than increasing cache limits as the default fix. For each implemented
separation, add deterministic work assertions at its canonical boundary, alongside
exact old-detail retrieval, pending-effect preservation and failed-save/restart checks.
Hold active work fixed while growing *that same owner's* completed history. Also vary
current subscribers and concurrent owners independently when validating publication.
Passing a recent-message window test does not establish bounded saves, admission,
agent reads or model context. Measure those boundaries separately.

Native history refresh obeys the same subscriber boundary as runtime progress.
`test_history_publication_scope.py` exercises resident roots and workers with a
viewing client and an unrelated Home client: progress must neither rebuild Home
nor rescan unchanged attention. Real title/error changes still reach both clients;
dormant catalog and workspace membership changes retain their broader scope.
This avoids multiplying publication by unrelated viewers without changing history
authority or claiming that the affected owner's save is history-independent.

Internal admission reads keep their loading fence in memory and commit the final
history or error once. They must not serialize the same retained execution merely
to announce that the read has started. Explicit client history loads keep their
loading publication. The same regression covers cancellation and a later read,
draft edits during the read, and unchanged canonical history without starting or
sending conversation work. Use the scaling fixture's `--operation history-load`
mode to compare cold admission and changed-history refresh across several owners;
check publication counts and exact durable history/execution as well as timings.

`tests/fixtures/execution_publication_scaling.py` keeps up to six large owners active
in one actual service. It distinguishes cold saves, scalar/token saves, nested
execution changes, current-placement changes with unchanged retired accounting,
and worker-report updates, checks exact durable readback and
records wall/CPU time plus the existing preparation budgets. Run it inside an
owned DTU with `python tests/fixtures/execution_publication_scaling.py --output DIR`;
`--source PATH` selects a frozen comparison checkout. A single-owner cache hit
cannot qualify concurrent-owner behavior or hide eviction churn.
Use `--reservation-bytes 512` to exceed a single accounting witness's size with
synthetic retained assignment details, and vary `--owners 1` through `--owners 6`
separately. This is a stress shape, not a measured live receipt schema. Keep the
same total preparation budgets in both runs: a larger history becoming cacheable
can still make concurrent owners slower through eviction and repeated bookkeeping.
Measure cold misses, current-placement changes and nested accounting changes;
unchanged whole-execution reuse alone would hide those costs. Retain rejected
comparisons when a proposed cache change improves one owner but regresses several.

Unchanged execution may reuse its existing committed projection only after the
original content, owner and resource evidence match; input digests and pending
flags promote with the same successful pointer commit. The content comparison
still visits the retained execution bytes. This short-term reduction in repeated
conversion is not proof of history-independent work under WS5. Keep measuring
that remaining cost; do not increase retention limits to make a benchmark pass.

For a synchronous private save, already canonical retired accounting can go
directly to the existing encoder after the complete input witness excludes custom
hooks. Check allowed fields, usage values and unique identities first; rows that
need filtering or ordered reduction keep the original detached projection.
Borrowed rows must not escape through an inline/public result or survive the save.
Nested alias edits must still change the exact durable JSON and content hash.
Prefer removing this redundant projection/decode work to retaining another graph
or increasing a cache budget. `test_execution_references.py` checks this path
against the canonical encoder and preserves public deep isolation.

The fallback retired-accounting preparation cache witnesses independent 128-row fragments
within the same 8 MiB witness and 64 MiB accounting budgets. It retains compact
JSON and witness digests, decoding detached rows for each save. Non-JSON values
keep the original projection. Encoding, accounting and committed/provisional
reference metadata share the existing 128 MiB total budget; accounting and
reference metadata each remain capped at 64 MiB. Encoding may borrow unused space
so several active histories do not cyclically evict one another merely because
one partition is full. A changed receipt
reprojects and sizes only its changed fragments; duplicate `(sessionId, id)`
identities and unsupported values keep the original whole-list reduction.
Size accounting charges retained text and metadata, not a second decoded graph.
`test_execution_preparation.py` checks changed-fragment work, cross-fragment
aliases, duplicate precedence, eviction, oversized fallback and six interleaved
histories without repeated fragment conversion/encoding;
`test_execution_references.py` retains pointer-failure/retry and resource checks.
Observed placements reuse the existing canonical JSON fragment cache after
witnessing each source fragment, its current turn membership and its exact
accounting receipts. Changed dependencies re-run the canonical projection;
unchanged fragments avoid repeating placement validation and JSON encoding.
Two pending-observation/voice facts accompany each canonical fragment under the
same content, turn and receipt witness. Saving unchanged fragments uses those
facts without decoding the encoded placements merely to derive reference flags.
This uses the existing encoding budget, not a new retained graph. Inline values
and unsupported inputs keep the original projection. The reference tests also
check changed receipts shared across fragments, removed turns, rejected rows
and small-budget fallback against complete canonical readback.
The tests also compare the pending flags and complete saved history after changed
voice lifecycle, removed turns and rejected placements, including a small-budget
fallback. This still traverses source witnesses and may decode fallback accounting
fragments; it is not history-independent publication or a fix for unrelated
storage GC delays.

For repeatable comparisons, `execution_publication_scaling.py --cycles 4` records
87 publications for three owners (three cold, 84 steady); `--size small` uses
four receipts and ten placements per owner. Run several fresh processes against
each exact source, alternate comparison order, and compare `executionSha256` and
publication counts as well as timings. `--profile` is a separate instrumented
run, not a latency sample. Process I/O counters include kernel writes beyond
application payload bytes, and peak RSS includes setup and final readback.
The `nested` event changes accounting usage (also aliased by its live placement)
and the current tool revision together. These are synchronous service publications,
not provider, browser, concurrent-dispatch or native cold-start measurements.

## Agent preparation and tool reads

Native checkpoint persistence and host notification are separate work. Save each
required transcript checkpoint, but publish its bounded collaboration anchors only
when their message/index/generation values change. A failed notification must not
be remembered as sent; a new worker must establish its own first notification.
`tests/test_host_session_resume.py` grows consecutive tool results while assistant
anchors stay fixed, checks exact saved results, changed generation/message anchors,
and notification failure/cancellation. The notification count must remain constant
for unchanged anchors rather than causing a full host save for every tool result.

`tests/test_agent_state_read_cost.py` grows retained question metadata while
holding the requested state fixed. Agent reads share the directory's existing
synchronous read frame across navigation, attention and workspace projection;
each section must not copy the same retained metadata again. An overview may
also prepare its independent frozen directory reader. This reuse ends before
yielding or returning: the next read observes current inputs, and mutation owners
still invalidate derived views. Explicit complete compatibility reads keep their
hydration path outside the frame. The directory frame checks cover exceptions,
inherited task/thread contexts, admission and durable/native changes. This bounds
duplicate preparation, not the cost of one preparation or all historical reads.

`tests/test_runtime_progress_backpressure.py` grows retained worker reports from
one to 475 with a large saved task body. Bridge diagnostics must produce zero
conversation saves/publications while remaining inspectable in pending and
durable diagnostic capture. Terminal execution receipts still commit immediately.
Inspection reads a bounded indexed diagnostic tail; absent or disabled capture
does not mean an operation succeeded. Historical captures retain their normal
retention policy.

`tests/test_preparation_context.py` checks one host preparation read per request,
explicit partial coverage, isolated concurrent requests and cancellation. The
existing surface/reaction/coordination/memory owners still construct their own
sections; unavailable data retains each consumer's existing behavior. The snapshot
is retained only with its bounded request record for the one unsent refit; a new
model request obtains a fresh snapshot. It does not replace memory or Work
revalidation at send. `tests/test_pretransport_refit_native.py` exercises the
real installed loop and selected-provider path with a timed-out image read:
the refit must not collect all context again, and changed/unavailable authority,
Stop and changed inputs must still prevent transport, including under Full access.
The native surface/awareness probes check selective images, fresh per-request
observations and clean durable history through the real runtime.

`tests/test_app_control_validation.py` exercises the mounted app tool with the
loop-live execution wrapper. Only host-declared independent reads may overlap;
dispatch and context/delivery mutations keep their ordering. Loop-live's real
engine checks preserve hook denial, delayed-approval revocation, cancellation,
call identity and default serialization. No model-provided flag grants this
behavior. Compare overlapping waits with a serial baseline; do not claim remote
provider latency or end-to-end live improvement from a synthetic transport.

## Quiet background observations

Quiet-watch storage has a separate history from client navigation observations.
`tests/test_observation_store_queries.py` holds selected reads and active work fixed
against 0, 100 and 1,000 unrelated retained watches. It measures actual SQLite
instructions and decoded rows/bytes, compares exact reports and list ordering with
the original scan, and preserves expiry, lease recovery and unknown handoffs.
Session/watch/state indexes must constrain SQLite work before body decoding;
fewer decoded rows alone can hide a broad index scan. The indexes derive from the
existing canonical JSON and do not rewrite it. Their first creation reads retained
records; this is not a constant-time startup claim. Complete public lists still
grow with the requested records, and genuinely active work still requires visits.

## Provider requests and cache observations

Keep reusable instructions, tools and accepted history stable while their meaning
is unchanged. Fresh request observations belong at the existing request-preparation
boundary; do not insert changing timestamps or reorder unchanged content ahead of
the reusable prefix. Check the final serialized provider request, including retries
and continuations, rather than assuming the host's message list is what was sent.

The provider owns wire caching policy: qualify the actual endpoint, API route,
effective model and deployment. An OpenAI-compatible name or inherited adapter
does not prove support for OpenAI cache fields. Reuse existing capability/endpoint
owners; do not create a host-wide model allowlist or a parallel cache manager.
Preserve caller settings, unsupported-route behavior and request input immutability.
Exercise more than one model tier and initial/continued requests. A repeat-prompt
probe establishes functional reuse, not a general latency guarantee.

Carry reported cache usage through both normalized responses and event telemetry.
A reported zero is a miss; absent usage is unknown. Do not invent cache writes,
hits or prices. Keep these distinctions in provider tests and host aggregation.
Measure request bytes separately from input tokens and cached tokens: a high hit
rate does not remove client encoding, uploads or server-side preparation work.

Historical images and tool results remain with the canonical transcript/context
owner. Removing them or rewriting opaque native items in a transport adapter to
meet a byte target is not context management. Qualify any selective context change
across resume, compaction, authorized retrieval and pending tool pairs before
adoption. See the [provider audit](provider-cache-audit-20261031.md) for the current
source checks and remaining limitations; it is not a runtime capability registry.

## Locating input acknowledgement delays

The existing bounded host performance snapshot records six fixed input phases:

- `input.history`: the send/retry dispatch's history-loading check, including a
  cold load or a warm no-op.
- `input.configuration`: that dispatch's configuration refresh.
- `input.runtime.prepare`: next-message configuration, worker readiness and any
  pending model selection. A mounted worker may make this a warm no-op.
- `input.runtime.admit`: waiting for the worker admission lock, any intervening
  retirement/restart, authority checks and the transport write for send/retry.
- `input.runtime.ack`: waiting for the native send/retry reply after admission.
- `input.delivery.publish`: successful delivery settlement, including the host
  state-lock wait, draft settlement, persistence and publication.

These use `operations` and the existing bounded `slowOperations` ring; they do not
require `AMPLIFIER_RUNTIME_TIMING`. The only correlation metadata is the existing
bounded conversation ID. They retain no input IDs, bodies, settings or exceptions.
Each completed phase is measured on error/cancellation too; a recorded duration
is not proof of success, delivery or replay safety. The final publication phase
is reached only after a successful native reply. Deferred input, steering and
passive delivery checks do not acquire new native send/retry phases.

Durations are inclusive wall time, including event-loop scheduling. In particular,
ACK time is not native CPU time, and publication includes lock waiting. History,
configuration, preparation and admission can contain other diagnostic operations;
do not add their totals to nested history/save/serialize/publication measurements.
These phases do not cover every dispatch step, concurrent inputs are not individually
correlated, and operation totals aggregate since startup. Use recent bounded samples
alongside the HTTP timing and current session state; an absent sample can mean a
phase was not reached or fell below the slow-sample threshold. This instrumentation
locates delays; it neither fixes them nor establishes a live latency guarantee.

## Maintained client smoke

`frontend/tests/client-traffic-browser.mjs` uses the real server and built client
with synthetic retained task history. It grows history from one to 128 large
records while holding current fields fixed; opens Work, details and runtime
panels; observes causal-only updates and reconnect; and reads a complete historical
record through paged details. It checks frame/projection growth, application-root
rendering, view-report feedback, current controls and unchanged canonical history.
Its byte budgets are scoped to that fixture; timings are diagnostic.

Causal-only samples wait for both shell requests and the selected conversation's
current, ready work receipt. The fixture verifies one initial native-catalog
read and stops only its periodic discovery loop: no external native history is
being added. Selected transcript/work readers remain active, and material frames
are never removed from a measurement. Delayed work reads must finish before
sampling; removing the revision-only render guard must still fail the unchanged
render assertion. See the [readiness follow-up receipt](client-traffic-readiness-20261030.json).

`tests/test_attention_demand.py` holds the viewed/resident set fixed while
growing acknowledged notice owners and their resolved-question history. Routine
views read existing notice facts without decoding unrelated session summaries;
selected details and bounded navigation receipts keep their exact fingerprints.
Global counts still aggregate scalar notice/read facts, and explicit Activity
retains all notices for review and mark-all. Neither path claims constant-time
global counts or a total byte bound. The check records SQLite work and diagnostic
CPU alongside decode/visit counts, and preserves native naming, workspace,
suppression, current read receipts and transaction boundaries.
Its native-catalog case keeps 220 notice owners and two residents fixed while
growing unrelated native rows from 300 to 27,000. Selected notices, visible read
receipts and explicit Activity must preserve their exact results without growing
SQL work with that unrelated history. This covers canonical native metadata for
local notice owners without scanning native-only rows that have no notices.

`tests/test_directory_scalar_payloads.py` holds the catalog and current notice set
fixed while growing retained body bytes. Scalar notice/revision queries must keep
their SQL inputs bounded by the requested metadata; full-detail reads retain the
exact bodies. Summary-only edits still invalidate observations, and frozen readers
retain their own state. The overlay encoding checks exercise mixed scalar/full
query order and reuse each variant only until the installed overlay changes.
These checks bound SQL inputs, not all live-summary preparation or global counts.

`tests/test_observation_receipt_cost.py` varies current read receipts against a
27,000-row native catalog and changes actual public facts to invalidate warm reuse.
One scalar join matches fingerprints and canonical owner roles; resolving those
same owners again in batches must not multiply preparation. Its semantic oracle
covers root/worker/internal roles, native suppression and stale fingerprints.
`tests/test_directory_page_cost.py` checks first, middle and late ordinal pages
without assigning a window rank to every owner. The existing directory-state
checks retain global positions, negative indices, private-state filtering and
stale-revision refusal. Their SQL budgets measure query work, not constant-time
global ordering or a guaranteed live-host latency.

The additive Extended browser registration runs after publication. The separate
`Client performance smoke` workflow selects affected PR paths and supports manual
runs; it is not a required status or a publication dependency. Run it locally in
the owned test environment with the production client built. Its synthetic runtime
does not qualify native recovery, model calls or installed-package provenance.

For nested programmatic tools, retain a large-row history work-count regression
(at least 36,000 rows, seven child completions): no full history read, native
store save or continuity export for verified inner completion, while each
control/writer check still runs. Qualify the actual Core/Live dispatcher and
ordered parent append, failed child results, context mutation, compaction and
Stop during a nested call. Untrusted or unavailable dispatch attribution takes
the full-save path. Ordinary direct-tool full-save counts must match the baseline;
do not add a second durable-append callback that doubles their persistence work.
Include actual Work-wrapped Full/incomplete-assurance children sharing the enclosing
checkpoint, and verify that their assurance eligibility remains unchanged.
This reduces nested completion work without eliminating the remaining enclosing
transcript persistence cost or adding a stronger crash-recovery guarantee.
