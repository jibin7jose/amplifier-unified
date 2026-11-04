# Parallel release qualification

The 0.20.8 release run [35803007224](https://github.com/microsoft/amplifier-unified/actions/runs/35803007224)
took 27m04s. Its serial stages included 7m34s of Python/frontend validation,
9m39s of runtime qualification, and 7m43s of browser checks. The runtime stage
spent 9m28s rebuilding the same Core Git commit as the preceding attempts;
its 24 actual tests took 4.57s. Application packaging and its install probe took
six seconds.

## Version choice and release notes

The release owner chooses the version from the combined changes since the last
published tag, and records the bump type and reason in the release PR.

- **Patch (`0.y.z` → `0.y.z+1`)**: repairs, performance, dependency/security
  maintenance, documentation and refinements to an existing workflow, preserving
  the supported contracts. Patch count and elapsed time do not force a minor bump.
- **Minor (`0.y.z` → `0.y+1.0`)**: a meaningful new user capability or workflow,
  a new supported client/integration surface, or a public-contract addition or
  deprecation. While on `0.x`, an intentional incompatible contract change also
  requires a minor bump, explicit owner review and migration guidance; do not
  hide it in a patch. Batch a coherent feature when useful, not one minor per PR.
- **1.0 and later**: declaring the stable supported public contracts is an
  explicit product decision, not a patch-count milestone. Thereafter follow
  [Semantic Versioning](https://semver.org/spec/v2.0.0.html): compatible fixes are
  patches, compatible functionality/deprecations are minors, and incompatible
  public-contract changes require a major. Bundled client/server edits still
  ship together; independently released clients retain their own contracts.

For **every release**, including fixes-only, dependency, packaging and test-only
maintenance releases:

1. Review the actual shipped diff/merged PRs since the previous published tag.
   Add its exact version to `amplifier_web/release-notes.json` with a short title
   and at least one useful plain-language change. Say what changed for users;
   if it is maintenance only, say so and identify the area. Do not copy a commit
   list, claim unmerged work, or describe an earlier pass as current verification.
2. Separately assess high-impact `notices`: changed defaults/permissions,
   migrations, existing workflows, automatic behavior or required action. State
   the effect and what the person should do. Ordinary fixes need an entry, not
   an attention badge; `notices: []` is valid. Preserve existing notice IDs and
   acknowledgments. Substantive wording corrections may require review again.
3. Check that skipped releases remain represented and that a person can tell
   what they are installing. Backfill omissions under the version that actually
   shipped the behavior, using verified source/release evidence. Deliver those
   corrections in the next package; never rewrite published tags or assets.
4. In the release PR, state the version rationale and whether high-impact
   guidance is included (or why none is needed). Use the existing release-notes
   tests, Fast release checks and package validation; no extra broad gate.

Keep the JSON schema wrapper and one complete release object per line, with
literal UTF-8 text and compact separators inside each object. Preserve that format
when adding an entry; expanding every nested field onto separate lines needlessly
consumes the bounded history budget. Formatting changes must preserve all parsed
values, including notice IDs, wording, aliases and release order.

The same data supplies installed/offline history and GitHub release text.
The validator already requires the current entry and a nonempty changes list,
and rejects invalid, duplicate or future versions; it cannot judge whether the
words cover the shipped behavior. Keep within `release_notes.py`'s bounded
history limits (512 entries and 512,000 UTF-8 bytes); plan retention deliberately
before either bound is reached rather than silently dropping notes. The entry
limit accommodates the authored backfill; the byte budget preserves the complete
history as it grows. Cached entries are validated in compact UTF-8 form, so JSON
spacing and ASCII escaping do not discard otherwise valid saved notes.

## Preparing a version change

Browser fixture launchers honor `AMPLIFIER_TEST_PYTHON` as one executable path.
Use the prepared environment's Python path when source and dependencies live
separately; a checkout-local `.venv` is not required. Pass the path as an environment
value without shell quoting inside the value or additional command arguments.
Existing per-fixture `FIXTURE_PYTHON` / `UNIFIED_TEST_PYTHON` overrides remain
fallbacks when the common setting is unset or empty, followed by the existing
checkout-local path. New launchers should use `fixturePython` from
`frontend/tests/fixture-python.mjs`. This chooses only the interpreter; readiness,
fixture arguments, browser installation and product assertions stay separate.

The frontend embeds the package version from `pyproject.toml` in both its
JavaScript bundle and `static/build.json`. After updating the Python package
versions (including the local `amplifier-unified` entry in `uv.lock`) and release
notes, verify `uv sync --locked --dry-run --group dev --group artifacts` in the
owned environment, then run `npm ci --prefix frontend`,
`npm test --prefix frontend`, and `npm run build --prefix frontend`. Include all
resulting `amplifier_web/static` changes in the release commit. Rebuild once more
and require `git diff --exit-code -- amplifier_web/static` to pass before merging
the release candidate; a version-only edit still changes generated bundle hashes.
Keep the release workflow's committed-assets comparison enabled when a matching
trusted PR proof is unavailable; a reused proof includes that same comparison.

The installed-wheel probe also loads worker-owned app packages in a fresh
`-I -S` child outside the checkout. A successful host import is not worker
qualification: sibling app packages must be exposed by the explicit bootstrap
without adding host site-packages or distribution metadata to the runtime.

When adding or changing optional runtime packages, refresh their entries in the
committed lock against accepted public sources and run the installed probe with
the affected extras. A default-host lock check does not qualify optional imports;
keep locked-install evidence distinct from fresh public-source resolution.

### Recovering a successful frontend build after asset parity fails

Fast release checks retains `frontend-build-output-<attempt>` immediately after
the build succeeds, before comparing the committed static files. It contains all
generated static files and a manifest of their hashes and exact non-static source
inputs. A stale-assets failure therefore does not require another owner to rebuild
and transfer the same output. Failed or cancelled builds do not supply this bundle.

Use the repository's exact run and attempt to download that artifact with `gh run
download`. Inspect the run's repository, source and successful **Build frontend**
step first; an artifact's own manifest is not independent workflow provenance.
In the task-owned checkout, run:

```sh
python scripts/frontend_build_artifact.py verify --directory /path/to/downloaded/frontend-build-output
```

Verification is read-only. It rejects changed non-static inputs, changed local
environment files, corrupt/missing/extra output, links and oversized bundles.
After verification, the owner can copy the bundle's `static/` contents into
`amplifier_web/static/` in its own checkout, removing only obsolete generated
files after checking for local work, then review and commit the asset diff.
If the source has changed, build that source again instead of importing old output.
This repair artifact never supplies release qualification or skips checks. The
existing successful-run proof and committed-assets requirement still apply.

Review generated assets through exact source identity, the verified file/hash
manifest and the resulting diff. Do not read every minified bundle into model
context before importing verified build output. A tool's read-before-edit rule
still applies when editing source through that tool; it is not a requirement to
read each generated file before a checked archive import. Preserve local changes
and reject changed inputs, unexpected paths or links before copying.

### Efficient post-release monitoring

Assign one monitor the exact published source and workflow run IDs. Passive
watching should wait for completion, not wake a model for each progress line.
With Bash, use a direct `action: run` with an explicit supported timeout and a
bounded/private progress log, then read the exact GitHub conclusion. Managed
`wait` wakes on output as well as completion; `wait_ms` does not extend the
process's start-time lifetime. Do not nest a long wait inside a shorter
`tool_exec` deadline. If the lifetime expires, inspect that exact remote run
before renewing within the original task deadline. Keep actual failures and
cancellation visible. This is a bounded tool call, not a durable detached watch
or authority to retry publishing, replay work or adopt the release on a host.

## Local-first, fast publication policy (updated October 14, 2026)

The development loop depends on published releases reaching local development
installations quickly. Target roughly two minutes for a warm publication run;
this is an operating target, not a guarantee about GitHub queues, downloads or
cold dependency builds. Exhaustive testing must not silently become a publication
or merge prerequisite again.

Before requesting merge, run the affected backend and frontend tests locally,
and exercise changed user flows in an owned DTU with synthetic state. Reuse owned
dependencies where appropriate. Aim for 1–3 minutes of focused local checks and
about five minutes for the normal foreground budget. The full local suite is not
a merge or release prerequisite, including for substantial integrations. Select
specific pre-release checks for material risks such as destructive migrations or
authorization changes. Fix failures attributable to the candidate's changed
behavior before publishing; record the exact source, commands, results, skips and
limits in the PR. Unrelated broad-suite findings remain visible follow-up work.
A green GitHub check is not a substitute for focused qualification. Follow
workspace DTU rules; never use a shared application installation as a test target.

For changes affecting repeated reads, publication, persistence or preparation,
apply [performance validation](performance.md) to the affected workload. Retain
measured budgets and semantic checks; this adds no blanket benchmark or exhaustive
suite requirement to merge or publication.

Typical local commands, inside the owned test environment:

```sh
uv sync --locked --group dev --group artifacts
uv run --no-sync pytest -q --tb=short tests/test_affected_feature.py
npm ci --prefix frontend
npm test --prefix frontend
npm run build --prefix frontend
git diff --exit-code -- amplifier_web/static
# Select relevant browser scripts from frontend/package.json.
# Optional broader local coverage runs separately in the background:
# uv run --no-sync pytest -v --tb=short
```

Replace the illustrative test filename with the actual affected suite. Real
runtime checks require their declared runtime dependencies; report host-only
skips accurately. Keep source, state, services and ports isolated and clean up
owned temporary resources after evidence is retained.

### Real filesystem-permission browser checks

`native-history-error-browser.mjs` and `standard-ux-storage-denial-browser.mjs`
require actual OS read/write refusal. Run their existing assertions as an
unprivileged fixture owner. On Linux, a root-owned disposable environment may
instead launch only the test process and descendants with `setpriv
--bounding-set=-dac_override,-dac_read_search
--inh-caps=-dac_override,-dac_read_search
--ambient-caps=-dac_override,-dac_read_search --no-new-privs --`.
Do not change the container's or shared service's privileges.

Before execution, prove that the exact child identity cannot read an owned
mode-000 file, then can restore its mode and read the unchanged bytes. Record
UID/GID, effective/permitted/bounding/ambient capabilities and `NoNewPrivs`.
Also prove that this identity can traverse and import the exact interpreter,
installed dependencies, browser binaries and evidence adapters. If a shared
dependency lives behind a private home, use task-owned byte-verified copies;
never broaden shared permissions. Keep source/package identities unchanged and
disable bytecode writes when reusing dependency directories.

A privileged read succeeding, a missing prerequisite or a startup error is not
permission-denial acceptance. Preserve those failed attempts separately. Retain
the ordinary browser assertions and strict process-owner cleanup result: both
cases must pass without residual-process retirement. This prerequisite applies
to local and CI owners; it does not add a universal release gate or establish
Windows/macOS permission behavior.

### Focused default-capability qualification

For changes to default composition, forwarding, desktop permissions or routing,
select these opt-in checks in an **owned isolated environment**. This is the
affected-feature check under the policy above, not a new universal CI/merge gate.
The ordinary broad workflow does not currently provision these prerequisites;
a skipped opt-in probe is not acceptance.

Prepare a noneditable candidate wheel and its pinned Python 3.13/Core/Foundation
runtime before testing. Record the wheel hash and dependency commits. Under
`UNIFIED_BEHAVIOR_REPOS`, retain checkouts named exactly as their repositories:
Foundation; bundles `context-intelligence`, `computer-use`, `routing-matrix`,
`imagegen`, `skills`, `modes`; modules `hooks-approval`, `loop-live`,
`loop-streaming`, `context-simple`, `tool-filesystem`, `tool-search`,
`tool-bash`, `tool-todo`. Install the packages declared by those manifests from
matching sources. The probe preflight lists missing agent-only declarations
rather than dropping agents or downloading dependencies.

Copy the candidate's `tests/` tree outside the application source checkout, then
run from another directory, with no provider credentials or host display:

```sh
export UNIFIED_RUNTIME_PYTHON=/opt/qualification/venv/bin/python
export UNIFIED_BEHAVIOR_REPOS=/opt/qualification/repos
export WARM_FOUNDATION_PATH="$UNIFIED_BEHAVIOR_REPOS/amplifier-foundation"
export WARM_ROUTING_PATH="$UNIFIED_BEHAVIOR_REPOS/amplifier-bundle-routing-matrix"
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
"$UNIFIED_RUNTIME_PYTHON" -I -m pytest --import-mode=prepend \
  -p pytest_asyncio.plugin -p pytest_aiohttp.plugin -rA \
  /opt/qualification/tests/test_builtin_behaviors.py \
  /opt/qualification/tests/test_default_capability_policy.py \
  /opt/qualification/tests/test_default_behaviors_integration.py \
  /opt/qualification/tests/test_default_runtime_safety.py \
  /opt/qualification/tests/test_default_mount_runtime.py \
  /opt/qualification/tests/test_default_routing_runtime.py \
  /opt/qualification/tests/test_default_export_literals.py
```

The finite routing probe loads the installed upstream hook, so stage the
unchanged `routing/` resource directory at
`Path(hooks_module.__file__).parents[3] / "routing"` **inside that isolated
runtime only**, recording and verifying its source hashes. This fixture layout
is not production packaging evidence. The separate full-mount probe uses actual
Foundation source activation and `ComponentResolver`, retains every agent,
substitutes exact existing skill directories for offline Git transport, and
checks shipped matrix discovery and real export without a bundle-root override.

Retain named results, skips, package/source identities and the negative-control
failure evidence. The suite checks actual CI target selection, real ComputerTool
write denial with a fake backend, nested/resumed desktop ceilings, real
Core/DelegateTool routing with finite providers, and preparation/mount/export.
It does not qualify live accounts, provider transport, real host desktop input,
browser consent or all desktop platforms. Do not substitute this suite for those
boundaries when a change affects them.

GitHub's required **Fast release checks** runs on every PR. It checks release
identity/reporting contracts, frontend unit tests, the build and committed assets.
Path-scoped contract workflows remain available for affected components. The
publication graph retains only prepare, package, frontend, native runtime and
publish. Matching candidate/dependency receipts, fresh installed-wheel probing,
immutable tags, checksums and the short real-runtime regression suite still gate
publication. The exhaustive readiness suite no longer runs on PRs or main pushes.

**Post-release verification** starts from successful completion of the publisher,
including publishers using `GITHUB_TOKEN`. It reads that run's retained candidate,
checks the actual published stable tag resolves to the candidate commit, and uses
that immutable revision for all broad tests. It does not substitute the current
main head or whichever release happens to be latest. The installed-package lane
downloads both published distributions, verifies their checksums, and installs
and probes the wheel outside the source checkout. The full Python, broad readiness, extended browser, and owned integration
suites run in independent jobs after publication. The [integration ownership
checklist](integration-coverage.md) maps optional-dependency skips to configured
execution lanes, runs affected lanes on PRs, and rejects unexpected skips. The
existing publication runtime group is not duplicated in that matrix.
The full Python lane has a 90-minute limit and prints each test's identity as it
runs, so a timeout does not conceal failed or unfinished test identities.
It retains the verbose log and, when pytest completes, JUnit results. A partial
log or absent JUnit file is not a successful or complete suite.

Extended browser checks use the explicit `frontend/tests/browser-checks.json`
plan, preserving distinct flags and modes. Scenarios run serially in separate
process groups, with a complete intended-coverage record written before execution.
A failed scenario remains failed while later independent scenarios run. The
runner retires only its owned process group and uniquely tagged descendants
(including detached Chromium processes) before proceeding; leaked processes
fail that scenario, and uncertain cleanup, cancellation or the bounded run budget
leaves the remainder explicitly unrun. Any failure or unrun scenario fails the
job. Per-scenario logs, durations, commands, declared environment, source/tree,
plan/runner hashes and build-metadata digest are retained with the run/attempt.
These are observations for that exact source, not reusable release receipts.
Fixtures must continue owning their synthetic state and cleaning resources;
runner process cleanup does not authorize access to real/shared installations.
Each scenario records existing PID/start-time pairs before launch. Unchanged
pre-existing processes are outside its inherited-token ownership; unreadable
new or reused identities still stop cleanup rather than being assumed unrelated.

Identical source-mode browser journeys have one background owner: extended
browser checks runs interim replies, draft reload, draft switch, activity feedback
and conversation navigation. Readiness does not repeat those five commands. Both
lanes check out the exact release, use Python 3.13 / Node 22, install the same
locked development dependencies and rebuild the frontend; these five invocations
have no distinct flags or environment. Their original assertions remain unchanged.

Other overlaps are retained for distinct setup: readiness runs feedback, message
outbox and canonical Work **after** reinstalling the package in its environment,
whereas the extended lane keeps its source environment. This is additional
package-install interaction coverage, not a claim of an isolated installed-wheel
probe (that has its own lane). Canonical Work explicitly selects multiprocess
Chromium there; its extended default is also multiprocess. Empty-host failure and
lost-response modes and the four session-health modes in the extended plan remain
separate because their arguments exercise different behavior. The full Python
suite remains the complete source oracle; readiness's focused Python checks are
retained as earlier recovery diagnostics, not a substitute for full-suite success.

Do not infer complete history from DOM Work-group counts. The reusable
`scripts/browser_dom_recorder.js` probe reports grouped headers and standalone
single-turn headers separately, excluding embedded turns inside expanded groups.
Schema 5 records the messages pane's canonical `data-session-id` separately from
optimistic navigation selection. Content milestones require that exact target
identity; an unpinned chat can therefore qualify after its workspace list
unmounts, without requiring a selected sidebar row. Older bundles without the
pane marker leave those milestones unqualified, and an old pane never qualifies
merely because its navigation/header changed.
Its minimum-header and settled-DOM milestones are visibility observations only.
Canonical turn/event completeness needs an explicit source oracle; regrouping
can raise or lower header counts without adding or losing work. Historical
captures made with older counting semantics must retain that limitation.

The reporting job writes a run summary and opens or updates one tracked issue per
release when any expected lane fails, is cancelled, is skipped or is missing.
A successful rerun closes its existing failure issue; green releases create no
issue. Test jobs have read-only repository access; only the isolated reporting
job can write issues. A failure does not withdraw the release, change its bytes,
replay work or modify installed hosts. Investigate it and ship a corrective release
when needed. Published and exhaustively verified remain distinct states.

Publication completion is the foreground handoff. Delegate monitoring and triage
of broad local and post-release verification, and continue the next task without
waiting for those jobs. Notify on actionable findings, not unchanged polling.
When a next release is already in progress, incorporate relevant corrections
there; otherwise address findings directly while idle. Newly discovered urgent
regressions still receive prompt attention. Reusing earlier qualification requires
matching the exact tested inputs; affected checks run again when those inputs
change. The frontend lane implements the exact-input reuse described below;
other passing runs are not blanket waivers for a later candidate.

The broad suites also remain manually runnable. Changing this policy requires an
explicit decision and an update to this document, tests and repository rules.
The repository's dedicated required-check ruleset names **Fast release checks**;
other review, deletion and history protections remain separate and unchanged.

## Execution and identity

`prepare` selects the same immutable merged application commit as before,
resolves current host dependencies once, and records their exact requirements.
Package, frontend, and runtime jobs then run independently against that commit
and restored host graph. Each verifies the graph before and after its checks.
The publication job requires all three jobs to succeed and checks their
candidate identifiers plus the original wheel/source/checksum bytes before
calling the existing immutable-tag publication implementation.

Qualification tools come from the workflow revision, copied before selecting a
historical application commit. They are transferred as an artifact within this
run. Package/native qualification and candidate receipts remain fresh.

The frontend lane may reuse an immutable artifact from successful **Fast release
checks** for the same-repository PR whose merge commit is this publishing main
commit. Reuse binds the entire tracked Git tree (a conservative superset of
frontend code, tests, scripts, lockfiles, version and build configuration), the
actual committed-assets digest, Node/npm and runner image identities. GitHub's
run, attempt, PR association, tested merge commit/tree, job/step success and
artifact archive digest are independently checked; the qualifying workflow and
proof helper must match the release checkout. A green check name or a JSON claim
of success alone is insufficient. The proof is emitted only after tests, build
and committed-assets verification, then retained seven days.

When GitHub explicitly returns an empty run PR-association list, the verifier may
confirm the exact run-head commit's associated PR instead. That independently
returned PR must match the merged PR, selected main merge commit, repository,
head and base commits, and main branch. Missing, malformed, or conflicting run
associations do not use this fallback. All other proof checks still apply; the
additional request shares the same lookup budget. Rejection diagnostics contain
only fixed reason codes and bounded counts, never raw API or exception text.

Lookup has a 20-second network budget and small archive/JSON limits. Missing,
expired, changed, fork, tampered or unrecognized proofs run the ordinary frontend
installation, tests, build and assets comparison. Historical release selections
always use that fallback. Backend-only changes can also cause a conservative
whole-tree mismatch; avoiding duplicated work never takes priority over exact
evidence. The frontend lane still issues a fresh receipt for the current release
candidate and retains a separate artifact stating whether and which proof was
reused. Broad post-release coverage is unchanged.

The runtime job checks out the declared loop-live source and current recipe
source as before. It resolves a new runtime lock with refresh and upgrade before
any build-cache lookup. Test dependencies, including tool-delegate, are now part
of this lock instead of being installed by a second unconstrained resolution.
The cache key includes the lock and project digests, loop-live commit, OS/image,
architecture, Python version/ABI, uv, Rust/C toolchain, Maturin version, and hashes
of relevant build flags. The recorded Maturin version also constrains isolated
builds. There is no fallback cache key or standing main-branch pin. A later
candidate resolves moving sources again.

Only a matching uv build cache is restored. Successful builds are saved before
runtime tests so that a test failure does not force a repeat compilation. Cache
contents are pruned with uv's CI policy, retaining source-built wheels. Runtime
graph and source identities are checked again after tests. The private loop-live
package is explicitly removed from this job's cache before saving it; access to
the dedicated-key checkout does not grant other cache readers its package build.

## Coverage map

| Previous serial stage | Job |
|---|---|
| Full pytest suite | Local, manual and automatic post-release workflows |
| Distribution verification, isolated wheel install and import/assets/login probe | Package |
| npm tests, frontend build and committed-assets comparison | Frontend |
| Browser scenarios and their conditional file guards | Local, manual and automatic post-release workflows |
| Real Core/loop-live surface, child, component and cache tests | Runtime |
| Immutable tag/asset checks and publication | Release, after all jobs |

In the manual workflow, the active-client performance check runs once; independent live-client behavior
is checked separately. All four session-health invocations remain, including context-limit and context-limit with an active
worker. Browser fixtures retain their serial ordering within an isolated runner;
fixed-port fixtures and performance checks do not compete with other lanes.

Artifacts explicitly enumerate tools, dependency records, lock/build identities,
qualification receipts and distributions. No service home, environment directory,
Git config, SSH key, or general environment-variable dump is uploaded. Build
flags are represented by hashes, not their potentially private values. Only the
publication job has repository write permission; the existing dedicated read-only
loop-live key is confined to its checkout step.

## Validation and remaining measurements

Regression tests reject changed app commits, dependency records, mixed lane
receipts, altered wheel bytes and changed worker graphs. Cache tests require
invalidation when sources, ABI, architecture, toolchain or build flags change.
Workflow tests require independent lanes, fresh resolution before cache selection,
and all matching receipts before publication. Actionlint validates the workflow.

The new runtime preparation commands were exercised locally against current
sources: locking completed in 3.10s, a fresh environment reused the cached Core
source build, and all 24 real runtime tests passed in 5.59s. These are local
measurements, not proof of GitHub Actions cache transfer or the complete new
release duration. No publication workflow was dispatched for validation.

After this workflow is merged, end-to-end validation can
select an already published tag's full original commit SHA. All jobs execute, and
the existing publisher leaves that tag and its published assets unchanged. The
default newer main commit with an already released package version is rejected
at planning; this guard prevents accidental publication but does not exercise
the downstream jobs. A new release still requires its own version and all required
qualification lanes.

The narrow frontend proof reuse described above does not promote an entire
previous release candidate. Reusing package/native qualification or shared
prebuilt wheels remains a separate proposal.

## Published Core binaries

The app host, worker project and Work acceptance project require
`amplifier-core>=2.0.2` from PyPI. This is a minimum supported release, not a
standing exact-version pin. Core's Git source override is removed. The uv
`no-build-package` policy forbids compiling Core; other source dependencies can
still build. The worker uses the same explicit public index as the host so an
outdated local mirror cannot silently keep it on an older Core release.

An existing worker's old app-owned Core Git-main default migrates only in a new
qualified environment generation. Its previous base manifest must prove that
default, and the installed source must match it. Forks, fixed refs and edited or
local sources are preserved; they are never treated as that default. Existing
locks and rollback receipts are unchanged. Installed-source augmentation must
not reintroduce the retired app-owned Core Git override.

The release's fresh source resolution, exact cache key and frozen runtime graph
checks remain intact. Rust/Maturin identity and build constraints remain because
other moving-source packages may require native builds. A Core policy/lock
change changes the cache key; no old Git-Core cache can make the registry
selection sticky. Core wheel metadata and its native extension hash now join
the runtime receipt, and qualification fails if amplifier-app-cli is installed.
No CLI companion is needed by Unified's runtime qualification.

To independently qualify a dependency change on both Linux architectures, run
the existing **Python checks (on demand)** workflow on the reviewed branch with
`core_wheels=true`. Its default remains the full Python suite. The Core mode
runs focused migration/receipt tests and all 24 real runtime checks unchanged
on Linux x86_64 and aarch64, using fresh moving-source resolution and published
Core wheels. It records the tested app revision, full lock, resolved source
identities, installed graph, wheel tags and native binary hashes. It has read-only
repository permissions, no publisher, and no production app access. The private
loop-live key remains confined to explicitly dispatched qualification; automatic
PR jobs do not receive it. macOS and Windows require separate platform evidence.
