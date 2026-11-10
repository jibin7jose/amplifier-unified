# Amplifier Unified — Fast Release Guide

**Release path:** prepare one PR → review while required CI runs → merge →
verify automatic publication and installed-package smoke.

**Target:** five minutes or less from routine version/notes preparation through
published-package smoke, once behavior is qualified. Include required CI and runner
queues. Record setup, orchestration, queue and required-check overruns separately;
the target is not a waiver or a claim that every release meets it.

## Establish the release owner and scope

Read `AGENTS.md` and this guide. Use a task-owned branch and preserve unrelated work.
Coordinate **one publisher and one selected version before preparing it**. Check
the latest published tag, current main, existing release PRs, publication runs and
other release owners. A free version check alone is not cross-session coordination.

Include all intended changes since the latest published tag. Qualify affected
behavior and obtain independent review before release preparation; reuse accepted
review and passing tests when their relevant inputs are unchanged. Hand off the
exact source, concise results, material limitations and retained evidence links or
hashes. Do not reconstruct a large evidence packet or commission another reviewer
solely because a contribution reached the release owner.

If another owner takes the version, reconcile that release with its owner. Do not
race it, replace its artifacts or automatically cut a second version. If releases
land between your releases, fetch current main and re-evaluate the unpublished
scope and selected version before preparation. Refresh dependencies only when
their inputs changed. Preserve authored source when resolving generated-asset
conflicts; rebuild once for the selected version rather than merging bundles by hand.

## Routine release

1. Choose the version and author the entry below, outside the source checkout.
2. In the owned prepared isolated environment, run the single preparation/build
   and locked dependency dry-run under [Preparing a version change](#preparing-a-version-change).
   Commit the four metadata files and generated assets in the change PR. A separate
   release PR is useful for batching already-merged contributions, not mandatory.
3. Push as soon as the candidate is ready. State the version rationale, user-facing
   scope, affected validation and limitations. **While required CI runs**, review
   only the remaining integration, version, notes and generated-output risks.
   Reuse independent feature review; add review only for risks not already covered.
4. Pass required CI, including **Fast release checks**, and satisfy repository review
   requirements. Merge the exact reviewed head promptly through authorized controls.
   Investigate relevant failures; do not bypass protections. Recheck unexpected main
   changes, but do not rebuild merely because unrelated files moved.
5. Verify the automatic release, then return the concise receipt under
   [Release completion and follow-up](#release-completion-and-follow-up).

Keep unnecessary work off the critical path: no fresh environment, repeated feature
review, broad suite, second manual build, large evidence archive or additional
reviewer by default. Use the maintained staging/build/output-transfer path rather
than authoring a new wrapper for every version. Keep evidence with its owner;
do not add acknowledgment loops, repeated status polling or waits for unrelated work.
After a timeout, inspect the saved operation or actual effect before retrying.

## Version choice and release notes

The release owner chooses the version from the combined changes since the last
published tag, and records the bump type and reason in the PR.

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
4. In the prepared PR, state the version rationale and whether high-impact
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

Write one entry JSON file outside the source checkout, replacing the sample values:

```json
{
  "version": "X.Y.Z",
  "title": "Short user-facing title",
  "changes": ["Explain what changed for users."],
  "notices": []
}
```

## Preparing a version change

Measure the five-minute routine release target from the start of version, notes
and build preparation through publication and the installed-package smoke,
including required CI and runner queues.

Prepare releases in an already-owned prepared isolated release environment,
or in a qualified author environment through its owner. Keep candidate source
and package checks isolated, but do not provision and finalize a fresh test
environment for each release when the existing dependencies and toolchain match.
The publication workflow owns the fresh exact-candidate installed-package proof;
a version change alone does not require rebuilding the local environment.
The in-app `workspace.tests.submit` path still freezes each new source; its
existing manual execute action does not refresh that source for a later
candidate. Queued callers reuse dependency layers until their owner provides a
supported prepared-source update path; do not invent a raw-host workaround.

Browser fixture launchers honor `AMPLIFIER_TEST_PYTHON` as one executable path.
Use the prepared environment's Python path when source and dependencies live
separately; a checkout-local `.venv` is not required. Pass the path as an environment
value without shell quoting inside the value or additional command arguments.
Existing per-fixture `FIXTURE_PYTHON` / `UNIFIED_TEST_PYTHON` overrides remain
fallbacks when the common setting is unset or empty, followed by the existing
checkout-local path. New launchers should use `fixturePython` from
`frontend/tests/fixture-python.mjs`. This chooses only the interpreter; readiness,
fixture arguments, browser installation and product assertions stay separate.

Use Python 3.13, uv and Node.js 22 with npm. Establish environment ownership and a
supported source-update path once; reuse the environment and matching dependencies
across releases. A new agent can take an explicit handoff instead of provisioning
again. Stage fresh task-owned source without overwriting another task's files or
running processes. New toolchains, changed lockfiles or missing dependencies require
preparation; a version bump alone does not. The dry-run below validates the lock,
not that every optional package is already installed or tested.

With the version coordinated and entry authored, prepare and build in one operation:

```sh
python scripts/release.py prepare \
  --version X.Y.Z \
  --notes-file /path/to/entry.json \
  --build

uv sync --locked --dry-run --group dev --group artifacts
```

It validates the notes and three version owners before changing them, preserves
unrelated edits and historical notice identities, and refuses conflicting notes
already saved for that version. An identical retry changes nothing. It neither
installs dependencies nor commits, tags or publishes. `--help` describes the notes
input; JSON output includes the remaining steps and a link to this guidance.
Expected preparation failures report a concise error and a nonzero exit status.
Without `--build`, the command emits the build step for the environment owner;
this split is for a necessary handoff, not the routine path. Avoid separate
model/tool turns for each version field, hash or repeated readback. A failed build
retains prepared metadata: fix the reported failure and rerun that build, rather
than reconstructing the candidate by hand or treating the failed attempt as passed.

The extracted frontend embeds its own package version in its JavaScript bundle
and `static/build.json`; it need not equal Unified's version. The normal build
verifies the selected client and records its source/input/build provenance in
`static/client-component.json`. See [client assembly](../../components/README.md).
Verify `uv sync --locked --dry-run --group dev --group artifacts`
in the owned environment. Reuse installed dependencies that match the current
lockfiles and toolchain; run `npm ci --prefix frontend` only when preparation is
missing or stale. Include the four prepared metadata files and all resulting
`amplifier_web/static` changes in the release commit. A host-only version change
does not restamp the client identity. Commit:

- `amplifier_web/__init__.py`
- `pyproject.toml`
- `uv.lock`
- `amplifier_web/release-notes.json`
- Generated changes under `amplifier_web/static/`

Review generated output through the maintained source/build manifest and hash
checks, not manual bundle inspection. Do not commit `release-source.json`; publication
generates it. The command does not replace scope review or version coordination.
Automatic publication watches the dedicated `amplifier_web/__init__.py` version
owner. Dependency or release-tool edits alone do not start a publisher; explicit
manual dispatch remains available with the same immutable identity checks.
Ordinary publisher pushes fetch only the candidate tree and its exact version
tag. An explicitly selected older manual target retains full ancestry to prove
that it was merged; remote lookup failures and conflicting tags remain failures.

Reuse focused behavior evidence for unchanged tested inputs. Do not add a local
full frontend suite or a second manual build solely for a version change.
Reuse independent review of the exact accepted feature source; the release
owner reviews the new integration/version/notes/assets delta rather than reading
every unchanged feature file and artifact again. Keep downloaded releases,
source archives and earlier test evidence outside the release source checkout:
snapshots correctly include untracked, nonignored files, so old output packets
otherwise become inputs to every later preparation. Preserve genuine untracked
source edits; use a clean task-owned release checkout when necessary.
**Fast release checks** still tests, builds and compares committed assets for the
candidate. A semantically verified version-only PR runs the maintained asset/build
smoke tests; source/dependency changes and unknown or manual comparisons run all
frontend unit tests. The proof names its actual test scope, never a skipped full suite. The publisher reuses only its verified exact-source PR proof, or runs
the frontend checks itself when that proof is unavailable; an earlier version's
proof does not qualify the new version. Package/native qualification and candidate
receipts remain fresh under the existing publication workflow.

Frontend proof reuse or the canonical build validates the staged
`amplifier_web/static` tree **before** packaging. Both the wheel and source archive
must contain its complete exact file set and bytes, including runtime-loaded
vendor files, build metadata and licenses. A published source archive is ready to
build into a wheel using Python build dependencies alone; installation never
downloads or builds the frontend. Raw Git development checkouts may require Node
and npm to produce updated assets; see [development setup](../../CONTRIBUTING.md#development-setup).
The source-archive round-trip regression exercises the real package backend with
Node absent and network/process creation denied. It is an affected packaging
check, not an additional universal release campaign.

For candidates declaring `/amplifier_web/release-source.json` in Hatch's global
`artifacts` list, the existing release tool stages that ignored file immediately
before packaging, after verifying the selected candidate receipt. Its deterministic
`schema: 1`, GitHub `repository` (`owner/repo`), full `revision` and package `version`
describe the clean source candidate. Wheel and sdist must carry identical bytes
matching those facts. The publication runner derives them again from its selected
checkout; it does not trust the package's own assertion. The sdist retains the file
when rebuilt without Git, Node or network access. Older manually selected candidates
without that declaration keep their original unstamped packaging contract.

Ordinary local/editable builds do not generate this record and require no release
setup. A retained record states source facts only: it does not prove publication,
unchanged delivery bytes, installed provenance or update eligibility. Those remain
separate qualification and runtime concerns. No build hook or runtime reader is added.

Routine release verification ends after the hosted published-package smoke.
Absent a concrete failure, do not repeat its exact-candidate wheel, source or
asset checks manually after publication. Record the workflow result and finish
owned cleanup; live adoption remains a separate authorized operation.

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
Build inputs with Git's `assume-unchanged` or `skip-worktree` flags must have those
flags cleared first so local edits cannot be hidden from verification.
To import it into an inactive task-owned checkout, run:

```sh
python scripts/frontend_build_artifact.py apply --directory /path/to/downloaded/frontend-build-output
```

Apply repeats verification, stages a complete replacement and removes obsolete
generated files. It refuses staged, unstaged, untracked or ignored local static
work, including empty directories. Already identical output is a no-op, preserving
the index. Review and commit the resulting asset diff yourself; apply does neither.

Rerun the same command after an interruption. One recovery directory under this
checkout's Git metadata retains the original files until replacement verification
succeeds, then is removed. A small reusable lock file remains. Recovery checks the
source, index and both trees; conflicting edits stop it with the recovery path so
you can preserve and resolve them. The `new/` tree inside that directory is
disposable staging, not a place to edit files. Keep the downloaded artifact until
apply completes. Checkout and Git metadata must share a filesystem. This is an
offline source repair command, not a live deployment or concurrent-editor protocol;
do not build or edit this checkout while it runs. Recovery covers process
interruption, not power-loss durability.
If the source has changed, build that source again instead of importing old output.
This repair artifact never supplies release qualification or skips checks. The
existing successful-run proof and committed-assets requirement still apply.

Review generated assets through exact source identity, the verified file/hash
manifest and the resulting diff. Do not read every minified bundle into model
context before importing verified build output. A tool's read-before-edit rule
still applies when editing source through that tool; it is not a requirement to
read each generated file before a checked archive import. Preserve local changes
and reject changed inputs, unexpected paths or links before copying.

### Verify automatic publication

Merging the version change starts **`release.yml`**, which qualifies, packages,
verifies and publishes immutable artifacts. Do not also create a tag, upload assets
or dispatch a competing publisher.

Confirm the publication succeeded at the intended merged commit, the release tag
and wheel/source-archive checksums match that commit and version, and automatic
**`post-release.yml`** installed-package smoke succeeded. Use those exact run/attempt
and candidate records; a green unrelated workflow or a saved build manifest is not
publication evidence. After an uncertain outcome, reconcile the existing operation
before considering a retry. Never move a published tag or replace release assets.

### Release completion and follow-up

Routine release work ends after immutable publication and the short published
asset/install smoke. Do not create a per-release model monitor, full-suite task,
or broad acceptance campaign. GitHub records conclusions and tracks failures;
assign an owner only when a failure needs investigation. A skipped nightly suite
is not evidence that it passed, and a smoke success cannot close its failure issue.
Return a concise receipt: **version · commit · release URL · publisher result ·
smoke result · material limitations**, with measured total time and any material
overrun. Absent a concrete failure, do not repeat the hosted package probe locally.
Reconcile owned resources, retaining prepared environments only with explicit
custody and cleanup responsibility.

Publication does not update running installations. Only the assigned live-update
operator performs adoption and verifies the new runtime identity without losing or
replaying work. Publication need not wait for adoption; the overall task stays open
when adoption is part of the request.

## Qualification policy

The routine graph consists of affected-feature qualification, required PR checks,
automatic publication and installed-package smoke. Keep exhaustive coverage with
its nightly/manual owner; moving hours of testing into the background for every
release is not the fast path.

Before requesting merge, qualify affected backend/frontend behavior and changed
user flows in an owned DTU with synthetic state. Reuse dependencies and exact
qualified inputs; do not rerun unrelated suites or paid acceptance solely for a
version change. Select specific checks for material risks such as destructive
migrations or authorization changes. Fix candidate-related failures before
publication and retain source, commands, outcomes and limits. A green CI check
is not a substitute for focused qualification. Follow workspace DTU rules.

Broad coverage runs once nightly around **01:00 America/Los_Angeles** from the
single schedule in `post-release.yml`, or by explicit manual investigation. It
must not also run automatically per release, PR or main push. Affected integration
lanes on PRs remain path-scoped. Remove duplication only with an identified
coverage owner; retain distinct installed-package, platform and media boundaries.

For changes affecting repeated reads, publication, persistence or preparation,
apply [performance validation](performance.md) to the affected workload. Retain
measured budgets and semantic checks; this adds no blanket benchmark or exhaustive
suite requirement to merge or publication.

Illustrative local commands, selected as needed inside the owned test environment;
this is not a mandatory every-release checklist:

```sh
# Prepare dependencies only when the existing environment does not match.
uv sync --locked --group dev --group artifacts
npm ci --prefix frontend
# Qualify changed behavior; reuse matching prior evidence for unchanged inputs.
uv run --no-sync pytest -q --tb=short tests/test_affected_feature.py
(cd frontend && node --test tests/affected-feature.test.mjs)
# For release version/notes/assets, use prepare --build above; do not build twice.
# Select relevant browser scripts from frontend/package.json.
# Full suites and committed-assets comparison remain with their CI owners.
```

Replace the illustrative test filenames with the actual affected suites. Real
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

GitHub's required **Fast release checks** runs on every PR. Only the existing
release-only comparison can select the small identity/notes and asset/build smoke:
it must prove all three application versions agree and no dependency, build
metadata or executable source changed. It then avoids repeating unchanged
release/reporting-harness and access tests. Other PRs, manual runs and unknown
comparisons retain those checks and the full frontend suite. All paths rebuild
and compare committed assets; package/native/publication proof stays unchanged.
The unfiltered nightly/manual browser owner also runs the full frontend suite
once, using its already-required frontend dependencies.
Path-scoped contract workflows remain available for affected components. The
publication graph uses one read-only qualification runner for preparation, package,
frontend and native-runtime checks, then an isolated write-enabled publisher.
Matching candidate/dependency receipts, fresh installed-wheel probing,
immutable tags, checksums and the short real-runtime regression suite still gate
publication. Exhaustive readiness belongs to nightly/manual verification, not PRs
or main pushes.

**Release smoke** starts after a successful publisher, including publishers using
`GITHUB_TOKEN`. It verifies that run's retained candidate and actual published
stable tag. The installed-package lane downloads both distributions, verifies
checksums, then installs and probes the wheel outside the source checkout. These
are the only automatic per-release checks after publication. Identity resolution
and installation share one read-only runner. Successful automatic smoke writes
its summary there and finishes; issue reporting gets a separate write-enabled
runner only for failures or explicit manual/nightly verification. Manual smoke
can resolve a previously reported smoke failure; broad and smoke failures remain
separate.

Nightly/manual broad lanes require the verified immutable release identity, not
successful installation. They can diagnose an install failure independently;
cancelled or unresolved runs stay skipped and unverified in the report.

**Nightly verification** freezes the latest actual published release and verifies
the latest successful canonical publisher's candidate/attempt against it. A
historical rerun mismatch fails visibly and can be corrected by dispatching the
matching publisher explicitly; it never silently tests an older release or moving
main. All broad lanes receive the same verified immutable revision. Manual
`post-release.yml` dispatch defaults to smoke; `broad: true` explicitly requests
readiness, full Python, registered browsers and owned integrations as well.

Readiness retains its distinct browser and installed-media checks. Source-unit
Python coverage belongs to the full Python lane. Full frontend units belong to
the unfiltered browser lane; readiness does not repeat them. The [integration ownership
checklist](integration-coverage.md) assigns optional dependencies and rejects
unexpected skips. The short publication runtime group is not duplicated there.
The full Python lane has a 90-minute limit and prints each test's identity as it
runs, so a timeout does not conceal failed or unfinished test identities.
It retains the verbose log and, when pytest completes, JUnit results. A partial
log or absent JUnit file is not a successful or complete suite.
The full-Python runner retains XML up to 8 MiB as `junit.xml`. Larger reports
use lossless `junit.xml.gz`, still capped at 8 MiB retained, with a streaming
validation pass capped at 32 MiB of source XML and a checked five-second scan
deadline. The 32 MiB scan budget accommodates the observed multi-megabyte
parameter identities plus ordinary suite cases without building a larger XML
tree. Unfinished XML tokens are bounded to 8 MiB plus one 64 KiB read; parser
attributes can temporarily occupy that bound. The receipt names the encoding,
file, retained hash and, for gzip, exact source bytes/hash. Compression omits
no identities, outcome elements or diagnostic content; it is not a summary
promoted to full JUnit. Declared counts must match observed structure through
valid EOF. Invalid, incomplete, over-budget or unsafe evidence never supplies
authoritative counts. Exit, interruption and cleanup still determine session
completion independently. The deadline is checked between bounded operations,
not hard preemption of filesystem/parser calls. These are finalization and
retention bounds, not limits on pytest's existing in-process XML construction
or pending-file growth. The verbose log retains its separate head/tail bound.

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
lane owns source-unit coverage; readiness does not repeat its Python subsets.

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
release and scope (smoke or broad) when any expected lane fails, is cancelled, is skipped or is missing.
A successful rerun closes its existing failure issue; green releases create no
issue. Test jobs have read-only repository access; only the isolated reporting
job can write issues. A failure does not withdraw the release, change its bytes,
replay work or modify installed hosts. Investigate it and ship a corrective release
when needed. Published and exhaustively verified remain distinct states.

Publication plus installed-package smoke is the routine handoff. Broad suites
belong to the nightly/manual owner, without per-release model polling. Follow up
on actionable failures, combining corrections with the next release when
appropriate; urgent regressions still warrant prompt attention. Reusing evidence
requires matching tested inputs; affected checks rerun when those inputs change.
The frontend lane implements the exact-input reuse described below; other passing
runs are not blanket waivers for a later candidate.

The broad suites also remain manually runnable. Changing this policy requires an
explicit decision and an update to this document, tests and repository rules.
The repository's dedicated required-check ruleset names **Fast release checks**;
other review, deletion and history protections remain separate and unchanged.

## Execution and identity

The publication workflow's `prepare` job selects the immutable merged application commit,
resolves current host dependencies once, and records their exact requirements.
Package, frontend and runtime checks share this read-only runner and host graph,
retaining separate candidate-bound receipts. Package bytes are built and recorded
before native checks prepare their separate environment; later source or dependency
changes still fail receipt verification. The isolated publication job requires
qualification to succeed and checks all three receipts plus the original
wheel/source/checksum bytes before immutable-tag publication. Build/test code
never receives repository-write credentials.

Qualification tools come from the workflow revision, copied before selecting a
historical application commit. They are transferred as an artifact within this
run. Package/native qualification and candidate receipts remain fresh.

The frontend lane may reuse an immutable artifact from successful **Fast release
checks** for the same-repository PR whose merge commit is this publishing main
commit. Reuse binds the entire tracked Git tree (a conservative superset of
frontend code, tests, scripts, lockfiles, version and build configuration), the
actual committed-assets digest, exact Node/npm, OS/architecture and runner image
family. Hosted-image patch versions are recorded separately for both runs but do
not invalidate frontend-only proof reuse; native build/cache identity is unchanged.
GitHub's
run, attempt, PR association, tested merge commit/tree, job/step success and
artifact archive digest are independently checked; the qualifying workflow and
proof helper and release-only classifier must match the release checkout. A green
check name or a JSON claim
of success alone is insufficient. The proof is emitted only after tests, build
and committed-assets verification, then retained seven days. It records `full` or
`release-smoke` explicitly; publisher reuse preserves that scope and never claims
full units ran for a version-only preparation.

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
reused. Broad coverage remains in the nightly/manual owner.

The runtime checks use the declared loop-live source and current recipe source.
They resolve a new runtime lock with refresh and upgrade before any build-cache
lookup. Test dependencies, including tool-delegate, are part of this lock.
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

| Boundary | Coverage owner |
|---|---|
| Full pytest suite | Affected local checks, explicit manual and nightly workflows |
| Distribution verification, isolated wheel install and import/assets/login probe | Package |
| npm tests, frontend build and committed-assets comparison | Frontend |
| Browser scenarios and their conditional file guards | Affected local checks, explicit manual and nightly workflows |
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

## Release mechanism checks

Regression tests reject changed app commits, dependency records, mixed lane
receipts, altered wheel bytes and changed worker graphs. Cache tests require
invalidation when sources, ABI, architecture, toolchain or build flags change.
Workflow tests require independent lanes, fresh resolution before cache selection,
and all matching receipts before publication. Actionlint validates the workflow.

For an explicitly needed publication-mechanism investigation, manual dispatch can
select an already published tag's full original merged commit SHA. Qualification
executes while the publisher leaves that tag and its published assets unchanged.
A newer main commit with an already released package version is rejected at
planning. This guard does not exercise downstream jobs. Routine releases use their
own coordinated version and automatic publication, not a second diagnostic dispatch.

The narrow frontend proof reuse described above does not promote an entire
previous release candidate. Reusing package/native qualification or shared
prebuilt wheels remains a separate proposal.

## Published Core binaries

The app host, worker project and Work acceptance project require
`amplifier-core>=2.0.2` from PyPI. This is a minimum supported release, not a
standing exact-version pin. App-owned declarations do not override Core with Git. The uv
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
selection sticky. Core wheel metadata and its native extension hash are part of
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
