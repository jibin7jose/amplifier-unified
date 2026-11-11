# Runtime preparation diagnostics

First-send preparation is separate from loading a saved conversation and from
waiting for a model response. A cold configuration may need dependency
preparation and compatibility checks before a worker can accept its first input.
The existing progress messages describe those stages; they are not delivery
acknowledgements.

Use [performance validation](performance.md) for startup and checkpoint changes.
Declare transcript size, retained job count and cold/warm conditions; separate
required recovery checks from repeated scans or copies. A no-op disk write does
not establish a cheap checkpoint, and faster preparation must preserve ownership,
native history, interruption evidence and cancellation cleanup.

Each newly prepared profile retains a private `profile-attempt.json` in its
generation receipt directory. It now records monotonic durations, in milliseconds,
for preflight, waiting for the configuration lock, the attempt overall, and these
phases:

- Source snapshot
- Runtime preparation
- Installer policy
- Dependency preparation
- Runtime freeze, including its individually recorded installation operations
- Compatibility probe
- Verification against the recorded installation

Each phase includes its start time and running, succeeded, failed or cancelled
status. Failed phases record the exception type. The receipt does not add command
arguments, environment values, output bodies or conversation text. Existing
private paths and source identifiers remain private diagnostic information.

Runtime freeze includes its nested operations: **do not sum all phase durations**.
The attempt duration is the enclosing interval. A process crash may leave a phase
marked running; that is a historical receipt, not proof that a process is alive.
A cached profile retains its original receipt, unchanged. These timings describe
qualification, not each later cache hit or first-token latency.

## Per-worker startup clocks

A worker retains its latest completed startup attempt in private
`runtime-reports/<session-id>/startup-timing.json`, beside its mount reports.
This is separate from profile installation and qualification. Fixed labels split
package bootstrap/imports, ownership/recovery, manager configuration and native
history load, bundle load/plan/preparation, provider setup, receipt repair,
session creation, context/children/settings restoration, initial checkpoint,
control restoration, app access, ownership registration, support metadata and
the worker's ready publication.

Each bounded row records monotonic elapsed milliseconds, process CPU milliseconds,
offset from worker-start entry, and succeeded/failed/cancelled status. Exception
class names are retained without exception text, arguments, configuration or
conversation content. The receipt identifies its attempt, process and start time.
Writes are private and best effort; a write failure cannot replace startup's
outcome or cancellation. It is written after startup cleanup and replaces only
the previous timing receipt. Failure before diagnostics initialize or a process
crash can leave no new receipt; verify its attempt and process before attributing
it to the current startup.

**Do not sum nested durations:** `manager.prepare` includes its manager phases.
Process CPU can include worker threads and does not identify one coroutine's CPU.
The final ready phase means the worker published readiness, not that the host
received it. These clocks exclude process spawning, prior profile qualification,
input admission and model response time; unlabelled intervals and teardown may
also appear in the attempt total. They diagnose costs without changing startup
order, eager restoration, provider selection or native-history authority.

## Startup cleanup ownership

The final worker compatibility probe and ordinary worker startup require
Foundation's canonical session activation API. Startup checks the actual loaded
worker interpreter before acquiring native ownership, restoring history or
admitting input. An older retained generation receives bounded guidance to
install included-component updates; its files and saved conversation remain
unchanged. The ordinary updater prepares and qualifies a new generation before
selecting it. App-only installation can leave the old runtime selected: this is
an execution-admission check, not protection against app promotion by an older
updater, and it does not silently refresh or rewrite a recorded generation.

Before registration, the startup coroutine owns any spawned process and its
generation lease. Awaited preparation-heartbeat teardown belongs inside that
ownership boundary: cancellation there must reap the process before releasing
the lease, clear preparation/reservation bookkeeping, and propagate cancellation.
No startup or task command may be written on this cancelled path. After successful
registration, the existing worker stop/close path owns cleanup.

Repeated cancellation must not abandon an in-progress reaper. A second cancellation
can suppress the best-effort stopped notification; cleanup must not depend on that
notification being delivered. If process exit cannot be confirmed because OS
termination fails, retain the lease and surface the failure rather than claiming
successful cleanup. Generation-lease scanning protects source storage and removes
dead-holder references; it is not a process reaper.

The real-child regression test `test_startup_heartbeat_cleanup_keeps_process_ownership`
selects cancellation at heartbeat teardown and again during reaping. It checks
child exit, exact lease-removal ordering, empty preparation/worker registries,
zero attempted startup writes, ordinary startup/stop, and post-spawn lease-acquisition
failure. This controlled schedule does not establish production incidence, model
execution, or performance improvement.

## Qualification

The affected profile-input and source-declaration tests cover successful timing
records, failed preparation and compatibility checks, nested freeze failure,
cancellation, private configuration exclusion, and cache reuse without rewriting
the original receipt.

An owned Linux DTU experiment on main `b190c3b7` plus this instrumentation used
synthetic configuration and no model input. Two concurrent cold Work-profile
requests shared one qualified runtime and completed in 11.50 and 11.46 seconds;
two subsequent cache hits took 1.18 and 0.98 milliseconds. Dependency preparation
accounted for 6.50 seconds, runtime freeze for 2.91 seconds, and compatibility
checking for 1.69 seconds. Download and OS caches could be warm.

This is not evidence of a latency improvement or Mac/Windows performance. The
subsequent actual worker-startup check found shared-source bytecode contamination;
that independent repair is tracked in PR #507. A passed preparation probe alone
does not prove a usable first conversation.

## Current-main delivery (0.20.109)

The cancellation fix is integrated with main 0.20.108. In the owned Linux ARM64
DTU, 162 runtime/lifecycle/update/profile/continuation checks and two subtests
pass; 85 release checks pass. The production frontend build passes.

The earlier hosted dictation failure is reproducible without a runtime change:
the test started with no selected chat, entered a draft, then created its first
chat. If autosave completed first, the new chat correctly inherited that draft.
Waiting for the authoritative draft save deterministically reproduces the
incorrect empty-draft assertion. The test now creates its source chat before
editing, switches to a distinct chat, and verifies the original draft survives
while late recognition results cannot enter the other chat. The corrected
browser test, cross-chat draft browser test, and three dictation unit tests pass.
This is simulated recognition, not physical voice acceptance. The failed probe
is retained in workspace evidence under output/approved-followups.

This release fixes the demonstrated pre-registration cancellation boundary;
it makes no broader CPU improvement claim. Platform acceptance remains separate.

## Source policy survives immutable installation

The qualified worker manifest pins exact installed commits for reproducibility.
Those commits are separate from the branch, tag, or explicit pin selected for
future updates. Before freezing a newly prepared graph, preserve an authored
requirement only when its repository, subdirectory, lock revision, and installed
revision agree. A full commit written by the resolver may then retain the
prepared declaration's original ref. An explicit installed tag, other branch,
fork, different directory, or changed revision cannot be relabeled this way.

Recovery needs explicit branch authority even when the resolver retains a named
`rev` rather than normalizing it into an exact SHA. Authored tags (including non-version names such as `stable`)
and ambiguous `rev`/PEP 508 names retain the exact installed commit. A matching
repository and commit do not prove that a name is a branch. Branch/tag conflicts
remain protected, even when their names are identical.

Only unconditional selected requirements and explicitly selected default groups
can authorize recovery. Marker alternatives, unused extras/groups, constraints,
and unknown source selection can block conflicting recovery but cannot authorize
it alone. The app's managed default group remains supported
through its actual checked stage receipt; an unchecked group URI is not branch
proof. Resolving ambiguous refs later requires selected-source reconciliation with
exact repository ref evidence, not a guess from names such as `main`.

Fresh runtime preparation mirrors retained fixed Git sources into resolver
overrides, so a dependent module's moving URL does not force a conflicting second
source for the same package. This preserves the selected pin; it does not recover
branch authority. Authored overrides and constraints remain protected. Generated
mirrors carry exact provenance and advance only with the separately verified
selected-dependency reconciliation. Before freezing the candidate, installed
version requirements and activated extras for those overridden packages are
checked independently, so source preservation cannot hide an incompatible version.

`runtime-sources.json` records `policyOrigin` beside each newly captured policy:
its basis, original ref, exact source identity, and (for prepared declarations)
the manifest digest. Ambiguous names use `unresolved-reference` and `originalRef`;
explicit tags use `tag-reference`. Both remain fixed until separate policy proof. Checked policies carry that origin through subsequent
staging and freeze. Exact source drift still fails qualification. Installed
owned clean cache modules retain their declared policy; unrelated or newer
adjacent caches do not explain a Git distribution's old pin.

Older pins without recorded origin remain unchanged. Their update-source detail
reports that the origin is not recorded, so an intentional pin is not silently
converted into branch tracking. Resolving a legacy policy requires the actual
source declarations and configuration; conflicting bundle declarations require
an explicit compatibility decision. This preservation step does not migrate legacy pins, alter registry component
policy, or refresh qualified test adapters. The separate reconciliation path below
can recover selected source policy in a fresh candidate.

Focused tests cover declaration recovery before freeze, direct/transitive
requirements, policy origin across stage/freeze/next check, exact identity drift,
explicit pins/branches/tags/forks, ambiguous declarations, clean owned cache
boundaries, and retention of an unknown legacy pin. They establish source-policy
behavior, not deployment or fresh-worker adoption on a user's installation.
Real local Git/uv tests also exercise normalized installs through production freeze,
recorded verification, and next inventory: explicit selected branches and checked
managed groups remain eligible; tags, ambiguous names, inactive markers and unused
extras remain pinned.

Actual authored-manifest resolver tests separately cover a repository with only a
`stable` tag, same-name branch/tag, and a real branch. Both `rev="stable"` and
PEP 508 `@stable` remain protected without branch-kind proof. The original-ref
evidence survives unrelated stage/freeze/inventory cycles.

Scope boundary: Foundation cache/shared-binding metadata can also contain an
untyped non-version ref. Its existing ownership checks establish source identity,
not branch-versus-tag intent. That adjacent path is tracked separately for exact
ref proof; this correction does not claim to resolve all cache policy ambiguity.

A universal lock's `branch` entry is not selected-intent proof: uv can collapse a
selected marker-specific SHA and an inactive branch at the same commit into that
one entry. Such conflicts stay exact unless an independent matching checked
policy already exists. Actual resolver regressions cover both marker selections.

## Cached source reference proof

The cache/shared-binding and raw installed-Git follow-up verifies an untyped ref against both its exact
branch and same-name tag in one remote response. Availability checks join identical
repository/ref lookups within the existing access scope; a cached branch-only
lookup cannot substitute for this proof. Tag-only and same-name branch/tag sources
remain protected, while missing/failed evidence is reported as a failed check.
A normal unambiguous branch continues to update without a name allowlist.

Fresh qualification also proves newly inferred cache-module policies at the
installed revision. Changed, unavailable or ambiguous evidence preserves the exact
commit with its original reference and reason. An already checked policy still
requires the existing installed-identity validation and is not reinterpreted.
Active and recorded generations are never rewritten for this check.

Real local Git/uv regressions exercise both legacy cache metadata and Foundation's
actual shared binding/build view through installed graph, production freeze,
recorded verification and next inventory. Separate production updater checks
cover tag-only, conflicting-name, ordinary branch and offline cases. Focused
tests cover grouped queries, access boundaries, fresh-check cache invalidation,
changed revisions, cancellation and preservation of checked policy. This is
isolated qualification, not deployment or live-provider evidence.

Real direct Git installs are checked before their first freeze, including a tag-only source,
a branch/tag name collision, and an ordinary branch. An exact qualified authored-branch
receipt remains authoritative even when a same-name tag exists. Unchecked transitive
references stay protected with their original name available for selected-source
reconciliation; they are not assumed to be permanently fixed by this check.

Fully qualified `refs/heads/...` and `refs/tags/...` names preserve their explicit
branch or fixed-tag intent; only unqualified names need the same-name collision
check. Other ref namespaces remain protected without inferred policy.


### Reconciling earlier generated pins

Older worker receipts may have recorded a resolver-normalized commit as their
standing update policy. The ordinary component Updates flow offers “Refresh included components”
when these receipts have no recorded origin or an unresolved reference. It does not load bundles while
checking or browsing settings. During an unpublished candidate only, a
collection-only pass observes the effective selected bundle, include and module
sources after configuration precedence, and binds them to the dependency
batch's exact package identities.
Host-selected app behaviors participate in that same observation: their winning
module declaration is recorded even when it replaces a root's or late module
choice. Installed and local host components remain local; explicit pins and
conflicting selected refs remain protected. Selection-version 3 rechecks earlier
no-op receipts that could have omitted these host-owned declarations. The
maintained source-policy tests cover an actual host-selected Bundle through
collection, checked Git refresh and preserved prior-generation evidence.

Only an unambiguous moving branch for the same repository and subdirectory can
replace an unknown-origin SHA. The candidate resolves that branch, aligns its
owned cache or shared-source binding to the exact checked commit, and collects
again before ordinary installation. A same-named branch/tag ambiguity, explicit
pin, fork, local override, separate resolver configuration, dirty source or
missing ownership evidence never grants migration. The normal installed graph,
source identity and immutable freeze checks still decide qualification.

An authored full-SHA dependency constraint can agree with an independently
selected branch only at the exact same repository, subdirectory and commit.
The collector retains every constraint and the declaring package/revision/
manifest digest, and classifies this agreement as `explicit-pin`, not moving-ref
authority. The existing pre-alignment guard requires the checked candidate to
equal that same commit even when ordinary branch alignment could advance it.
A future branch revision is refused before checkout or installation; the pin
is not removed, the selected ref is not rewritten, and legacy-pin migration
does not promote it to branch-following permission. Abbreviated pins, tags,
unknown/conditional alternatives, forks, different subdirectories and any
conflicting declaration remain protective. This is same-identity compatibility,
not a change to authored source policy or installed-generation ownership.

A generation receipt records migrated, protected and unselected packages.
A receipt matching the current configuration and exact installed identity marks
packages absent from the offered-profile batch as usage unknown, kept for existing
chats. It does not claim they are globally unused or remove them. Changing profile
configuration invalidates that classification.
Unselected catalog providers remain unresolved; merely offering a provider
does not select its source. The receipt suppresses repeated no-op update
prompts for the same configuration. If another component already prompts a
fresh candidate, remaining legacy decisions are reconsidered against that
candidate's selected declarations. Existing generations and rollback evidence
are unchanged; this is not a live-worker dependency rewrite.


Selected static root manifests also seed a dependency closure. The collection
pass follows applicable installed Requires-Dist edges using the candidate's
marker environment and explicitly requested extras. An installed distribution
outside that closure cannot authorize recovery. Dependency-only Git choices
must match the current installed package, repository, subdirectory and commit;
conditional Git declarations, conflicting pins and local choices stay protected.

For a proven moving dependency that is not a direct prepared source, the fresh
candidate replaces only its exact generated old-SHA manifest entry with the
checked commit. It refreshes and verifies that resolver lock, installs it into
the candidate, then recollects selection and runs the ordinary freeze checks.
The serving generation and its old receipts remain unchanged. Reconciliation
receipts include a selection-version marker so earlier direct-only no-op results
receive one recheck rather than permanently concealing reachable dependencies.

Qualification covers real local Git and uv installs where a dependency-only
@main advances from an old generated SHA, freezes exactly and remains eligible
on the next inventory. Tag-only and same-name branch/tag cases retain the old
commit. Additional tests cover markers, requested extras, fixed version edges,
unused packages, explicit pins/forks/local sources, and changed generated
manifest entries. Dynamic or unverifiable dependency metadata remains
unresolved; these tests are not production adoption evidence.
