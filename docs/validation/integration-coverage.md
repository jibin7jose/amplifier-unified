# Integration test ownership

The ordinary host suite may omit optional runtime dependencies. That does not
qualify those integrations. `scripts/integration-coverage.json` assigns every one
of the 87 skip records audited from revision `d1735f82` to an execution owner.
Three records skipped entire modules, so 87 is not the number of test cases.
Later opt-in checks have separate `additionalRecords`; the historical baseline
and its 87 records remain frozen.

## When checks run

- **Affected pull requests:** `Owned integration checks` selects lanes using the
  manifest's changed-path rules. Shared runtime/dependency/runner changes select
  every automated lane. Documentation-only changes do not launch integrations.
- **Nightly:** `Release smoke and nightly verification` calls every automated lane
  against the immutable published revision. Its report includes the integration
  result; failed, cancelled, missing, or skipped jobs cannot count as success.
- **Manual:** the same automated matrix can be dispatched on a selected ref.
  Installed-PWA, optional M365 overlay, unpublished profiling and installed-default
  checks have explicit manual owners below. None is implied by a green headless
  or host-only run.
- **Publication:** the existing short native-runtime group remains the owner of
  ten audited opt-in cases. It now verifies its JUnit report for skips and missing
  required selectors. It is not rerun as another integration lane.

The single schedule in `post-release.yml` owns broad coverage around 01:00
America/Los_Angeles. There is no second integration schedule or automatic broad
per-release campaign. Publication, installed-package smoke and broad verification
remain distinct.

## Accountable checklist and initial results

These IDs extend the standard UX smoke inventory with integration prerequisites.
All automated lanes require **zero unexpected skips**, zero errors/failures, and
execution of every declared selector. Missing packages, source permissions,
servers, browsers, displays, or filesystem behavior fail their designated owner.

| ID / owner | Coverage | Initial owned-DTU result | Desired result |
| --- | --- | --- | --- |
| IC-01 native | Real Core, Foundation, loop, provider serialization, context, native jobs, kernels, processes, attachments, terminal, controls, warm/history workers | 77 passed, 0 skipped; 35.50s execution with clean local loop source | Same coverage on affected PRs and nightly |
| IC-02 release-runtime | Existing guidance, child/recipe lifecycle, module cache group | Release 0.20.167: 109 passed, 0 skipped in the existing group | Keep the existing short publication owner; reject new skips |
| IC-03 browser-headless | External-link authentication, service-worker forwarding, narrow/wide setup renderer | 3 passed, 0 skipped; 5.91s | Reproducible disposable Chromium on affected PRs and nightly |
| IC-04 browser-installed | Real installed-PWA launch and entry behavior | Not qualified by this Linux/headless run | Explicit installed-app/display acceptance for changes to PWA launch/install/entry behavior |
| IC-05 cli-interop | CLI → Unified → CLI history, ownership, provider settings and portability | 3 passed, 0 skipped; 4.15s with freshly prepared CLI environment | Same execution with recorded CLI and loop revisions |
| IC-06 filesystem-aliases | Concurrent create cannot overwrite case/Unicode filename aliases | 3 passed, 0 skipped on GitHub macOS; Linux prerequisite probe correctly rejects non-aliasing filesystem | Both alias variants execute on macOS; a skip fails |
| IC-07 uv-reset | Actual package corruption/missing-dependency repair using uv | 1 passed, 0 skipped; 0.62s in fresh prepared environment | Repeat on reset/dependency changes and nightly |
| IC-08 context-intelligence | Real isolated server/database, authenticated graph routing and content redaction | 1 passed, 0 skipped; 2.96s | Real disposable Neo4j + server, no production endpoint or account |
| IC-09 connected-documents | Native upstream composition, shared UI/agent invocation, policy and idempotence | 2 passed, 0 skipped; 1.62s against explicit local upstream copy | Manual optional-overlay acceptance; synthetic Graph transport, no live account writes |
| IC-10 profiling | Real optional capture library, revocation, privacy, retention and receipts | 10 passed, 0 skipped; 10.28s against clean local library `8edea47c3a05eb4e6206211ac993a0a673245b8a` | Repeat against an explicit clean source; automate when canonical source distribution is available |
| IC-11 Stories joined | Actual worker storage → real Stories MCP import/retained read → native MCP App review/export/reconnect | Passed against explicit source and supported Agent; separate fresh-install defect repaired upstream | [Manual optional-integration owner](stories-joined-acceptance.md), required on affected changes/source adoption; missing prerequisites fail, never skip |
| IC-12 native additions | Root/child read policy with actual filesystem/search, both ripgrep and Python fallback; default capability inheritance; Foundation export roundtrip | Registered separately from the 87-record baseline; source-bound execution receipts report actual results | Existing native owner on affected PRs and nightly; current tool packages and ripgrep required before pytest |
| IC-13 default-installed | Real offline behavior composition, runtime destination/write policy, full mount/export and finite Core/DelegateTool routing | Explicit installed-wheel owner; registration is not a claim of new acceptance | On changes to these boundaries, run copied tests with prepared exact sources and enforce every selector with zero skips |

The initial [GitHub matrix](https://github.com/microsoft/amplifier-unified/actions/runs/38873044197)
passed all six automated lanes: 88 tests, zero skips, including the macOS cases.
Each lane recorded unchanged source throughout execution.

### Child host-component and ownership coverage, October 17

The native owner now explicitly requires both dynamic and resumed variants of
`test_dynamic_and_resumed_child_plan_reuses_host_sources_and_instances`.
These optional loop-live cases were missing from the ownership manifest. Their
two additive records leave the frozen 87-record baseline unchanged, and each
parameterized selector must appear in the owner result without skips.

The same native owner explicitly requires the real shared-handle parking,
failed-save handoff and cooperative worktree-release cases. Their three additive
records require Foundation's actual same-user socket and ownership mechanisms.
Use a short private `TMPDIR`: Foundation appends its handoff directory/socket and
rejects endpoint paths longer than 103 bytes. A deeply nested qualification
temporary root caused nine original census failures before those affected
boundaries could be qualified. Keep those failed receipts; corrected temporary
storage and fresh execution establish separate results, without changing the
product, removing assertions, or treating setup failure as a pass.

The check preserves the host's qualified loop/source choices, named hook
instances, merged child configuration and original overlay. Its fake parent
advertises only the intended `session.working_dir` capability; absent provider
isolation is `None`, as on a real coordinator, rather than a working-directory
string masquerading as every capability. The real provider-isolation contract
and all child-plan assertions are unchanged. This preparation check deliberately
stops before creating a session or calling a model; it is not provider-execution
acceptance. Current execution receipts are retained separately from the failed
full census at `af08e2d56f9197df37a32c79ffe94b829be4c855`.

Times in the table are test execution observations on spark-2, not CI provisioning targets.
These native/provider tests use deterministic offline providers; they do not
prove paid-provider latency, real Microsoft 365 authorization, or live Office
client acceptance. Their integration claims concern the actual native modules
and host boundaries exercised by the named tests.

### Checkout handoff admission, October 21

The existing native owner requires
`tests/test_handoff_native_admission.py::test_actual_native_turn_completes_handoff_without_self_wait`.
Handoff, model-admission, execution telemetry and related fixture changes select
this owner. It installs the actual native loop prerequisites and rejects missing
or skipped execution. The additive record preserves the frozen 87-skip baseline;
it does not add a publication gate. The coordination host group also runs the
ordinary handoff admission guard and Stop/provenance regressions.

The native case uses a deterministic provider with actual Core, loop-live,
AppService and model admission. It verifies the tool reply, next model response,
generation completion, saved context and one handoff placement, while new input
remains fenced until settlement. The final process-release boundary is a fixture;
this is not live-chat or paid-provider qualification.

### Terminal lifecycle regression, October 16

The PR812 native run `38968575306` timed out waiting for its stop-test provider.
The fixture sent that new-turn input after seeing assistant text but before the
previous generation finished. A delayed assistant-event observation reproduced
an explicit held-steering receipt; it was not evidence that stop had failed.
The fixture now waits for the terminal client's idle snapshot and asserts an
accepted, non-steering receipt before waiting for the provider. The actual
two-client detach, duplicate delivery, reconnect and cancellation checks remain.
The existing IC-01 native lane owns this check; no additional lane or retry is
added. Normal execution and the same 750ms delayed-event probe passed locally;
the original hosted failure remains recorded separately.

## Running an owned lane

Use an owned disposable DTU locally, with private state, dependencies, services,
ports and browser context. CI uses disposable hosted runners. Never point these
checks at an active user service. Source copies must be clean and revision-bound.

The workflow checks out the declared dependencies under `.ci/`, then invokes:

```sh
python scripts/run_owned_integration.py --lane native \
  --work /owned/new-lane --output /owned/evidence/native
```

The helpers prepare prerequisites without changing the shared development
preview. The common runner retains JUnit plus a result receipt, hashes source
before and after execution, rejects changed inputs, removes stale result files,
and rejects collection skips as well as per-test skips. It verifies selector
presence, not just pytest's exit status. Preparation records include resolved
sources and installed packages; the native lane also retains its lock/project.
Only evidence is uploaded, never private homes, caches, credentials or user data.

For installed-PWA acceptance, use `integration_clients.py prepare` and
`preflight` with `--lane browser-installed`, a new private state directory,
`AMPLIFIER_TEST_INSTALLED_PWA=1`, and an actual display. Pass the prepared
interpreter/environment to `integration_checks.py run --lane browser-installed`.
A headless substitute does not satisfy this owner.

The existing installed-wheel adapter also provides two **separate opt-in**
hosted stages. They do not join the automatic matrix or change IC-13/desktop
defaults. On an explicitly reviewed candidate, select **one**:

```sh
gh workflow run integration-checks.yml --repo microsoft/amplifier-unified \
  --ref REVIEWED_CANDIDATE_REF -f root_guest=true
# Or, separately:
gh workflow run integration-checks.yml --repo microsoft/amplifier-unified \
  --ref REVIEWED_CANDIDATE_REF -f installed_pwa=true
```

The reusable/direct `default-installed.yml` accepts the same flags; the fixed
adapter arguments are `--root-guest` and `--installed-pwa`. Conflicting stages
fail before preparation. This is a route definition, **not a qualification
result or permission to dispatch**.

The root owner runs all eight unchanged `test_durable_guest_jobs.py` cases in
an offline, read-only, private-PID container. Only SETUID, SETGID and KILL are
added after dropping all capabilities. The Python 3.13 supervisor remains
root; workloads use actual `/usr/bin/python3` as UID 65534. Temporary ancestors
are traversable, but supervisor controls and receipts stay private. Preflight
must demonstrate the UID transition, `/proc` identity reads, cross-UID
termination and denied workload access to private control storage. This never
runs pytest as root on the host.

The existing `browser-installed` owner retains its single unchanged behavior
case. Its adapter stage uses an unprivileged container, a private Xvfb display,
and Python Playwright/Chromium matched to the frontend lock. Browser preparation
is reused without running `uv sync` or replacing the noneditable candidate
installation. The real headed CDP install, login, launch, relaunch and uninstall
remain required; overlay assertions stay conditional on the browser's reported
overlay. A failed uninstall still closes the browser context and still fails.
Only the bounded synthetic `installed-pwa.png` and `installed-pwa.json` are copied
from pytest temporary storage before teardown, never the profile, cookies or
app state. This is Linux headed-browser evidence, not physical-device, macOS,
native-control or dragging acceptance.

Both stages require exactly eight or one unique named JUnit cases, respectively,
with no extras, skips, failures or errors; nonzero exit or unresolved cleanup
cannot pass. They retain the existing two-CPU, 4-GiB, 256-PID and workflow limits,
one running owned container at a time, exact source/wheel/dependency identity
before and after, and live-tmpfs evidence transfer before removal. The new
stages additionally attest wheel files, system prerequisites and browser files.
Missing binaries or unsupported display/CDP behavior fail visibly; do not
substitute headless execution or widen privileges. Shared base-image cache is
retained and is not counted as owned teardown.

For profiling, prepare a native environment with
`integration_native.py prepare --profiling-source file:///clean/library ...` and run
`integration_checks.py run --lane profiling` in that environment. The library
must retain its real implementation and exact source identity. Missing library
access is pending qualification, not a successful skip.

## Access and maintenance

The private loop source uses the existing read-only CI key. M365 tools are not
part of Unified’s standard shipped dependencies. Its two tests qualify an
optional developer overlay, so they remain manual: provide a clean upstream
checkout under `.ci/m365` and the declared `.ci/loop-live`, then explicitly run
the `connected-documents` lane. No new cross-repository CI access is needed.
The local profiling repository currently has
no configured remote, so it remains a manual owner until publication is decided.
Fork PRs cannot consume private repository secrets; their private-source lanes
remain unqualified until an authorized source-access run is available. Do not
use `pull_request_target` to execute untrusted PR code with secrets.

When adding an integration or discovering another skip, update the ownership
manifest, affected-path rules, this checklist, and the nearest UX smoke scenario.
When a selector is removed or renamed, update its owner deliberately: a missing
selector is a failing result, not permission to shrink coverage silently.

### Additive runtime owners, October 16

PR817's read-policy suite is module-skipped without loop-live and additionally
needs current filesystem/search packages and real ripgrep. The existing native
owner now prepares those packages, verifies imports and records `rg --version`;
its CI job installs ripgrep explicitly. The tests still exercise the Python
fallback as well. Stale tool implementations or missing binaries cannot become
a successful owner run through a skip or fallback-only pass.

PR815's default-capability policy tests and its Foundation export roundtrip use
the same already-required native dependencies. They join that lane. Ordinary
builtin-behavior and export unit cases remain in the full Python suite.

The four `default-installed` suites require a noneditable candidate wheel,
source-bound offline behavior checkouts and their matching installed modules.
The native lane intentionally uses an editable Unified checkout, so it cannot
substitute for that proof. The Smart Tools/runtime integration owner runs these
checks in an owned container on changes to composition, forwarding, desktop
policy, routing or the qualified dependency set, before claiming those boundaries
qualified. This is explicit affected-feature acceptance, not a new universal
publication gate or an automatically provisioned hosted lane. Its fake desktop
backend and finite providers do not exercise physical input or paid accounts.

Prepare the exact prerequisites and copied `tests/` tree described in
[release qualification](release-qualification.md#focused-default-capability-qualification).
Set the documented runtime/source variables, then run outside the application
checkout. The root below contains only the copied `tests/` tree; the interpreter
contains the candidate wheel. Both commands must succeed:

```sh
"$UNIFIED_RUNTIME_PYTHON" -I -m pytest --import-mode=prepend \
  -p pytest_asyncio.plugin -p pytest_aiohttp.plugin \
  --rootdir=/owned/default-qualification \
  --junitxml=/owned/evidence/default-installed.xml \
  /owned/default-qualification/tests/test_default_behaviors_integration.py \
  /owned/default-qualification/tests/test_default_runtime_safety.py \
  /owned/default-qualification/tests/test_default_mount_runtime.py \
  /owned/default-qualification/tests/test_default_routing_runtime.py
python /owned/candidate/scripts/integration_checks.py check \
  --lane default-installed --junit /owned/evidence/default-installed.xml
```

Retain the candidate wheel/source identity, prepared dependency receipts, complete
log and JUnit alongside the check result. Missing prerequisites, module skips,
per-test skips or absent selectors mean **unqualified**, even if ordinary pytest
exits zero. The isolated installed probes reject checkout/editable imports;
keep their original guards rather than bypassing them to fit the native lane.

### Installed desktop safety continuation

The `default-desktop-safety` manual owner covers the whole
`tests/test_default_desktop_safety.py` module, including its optional collection
prerequisites. It uses the existing installed-default adapter with
`--desktop-safety`, **not** the `default-installed` IC-13 command or a generic
pytest lane. Manual planning includes this owner for affected paths; automatic
planning does not launch it.

On the exact reviewed candidate ref, dispatch the existing integration bridge:

```sh
gh workflow run integration-checks.yml --repo microsoft/amplifier-unified \
  --ref REVIEWED_CANDIDATE_REF \
  -f default_installed=true -f desktop_safety=true
```

The reusable/direct `default-installed.yml` workflow accepts the same
`desktop_safety=true` input. Its hosted invocation supplies the adapter's required
arguments:

```sh
python scripts/qualify_default_installed.py run --desktop-safety \
  --loop-source "$GITHUB_WORKSPACE/.ci/default-loop" \
  --work "$RUNNER_TEMP/default-installed-inputs" \
  --output "$RUNNER_TEMP/default-installed-evidence"
```

The private loop checkout must match the committed pin and must not carry
credentials into the guest. The adapter requires a GitHub-hosted Linux runner;
this command does not authorize using a live installation or a borrowed runtime.

Require separate `desktop-preserved-22.xml` and `desktop-remote-defaults.xml`
results: all **22 preserved cases plus 3 remote-default cases** must execute
without failures, errors, skips or missing/duplicate identities. The adapter
checks those fixed counts in `desktop-safety-result.json`; generic module
presence in JUnit is insufficient. Adapter helpers are reported separately.
Retain installed wheel/source/dependency identities, unchanged before/after
attestations, both group receipts and confirmed owned-resource cleanup.

This stage reports `baselineExecuted: false`. It neither runs nor inflates the
separate IC-13 baseline's four modules and 12 cases. Registration is not
acceptance, physical-desktop or SSH-transport qualification, or permission to
enable desktop writes.

### Installed local status owner, October 17

The `status-installed` manual lane owns both executable status probes. The
release/runtime maintainer must run it on changes to the emitter/protocol,
execution-event boundary, relevant worker lifecycle, wheel packaging or the
qualified CLI/Core/Foundation dependency set. It is affected-feature acceptance,
not a generic pytest lane or an automatic hosted job. Focused emitter unit tests
remain in ordinary CI. Missing prerequisites fail this owner rather than skip.
`integration_checks.py plan --include-manual --changed /owned/changed-paths.txt`
lists this owner when relevant; omitting `--include-manual` preserves CI planning.

Prepare an independent installed CLI interpreter with canonical Core/Foundation,
loop-live/context-simple and the **candidate standalone status wheel**. It must
not import Unified. Prepare the normal Unified native integration interpreter
separately, without the CLI. Keep both read-only during qualification. Supply the
candidate Unified/standalone wheels and the published native Core wheel required
by the candidate. Core's filename/hash is checked against PyPI publication
metadata, and installed host/CLI Core package bytes must match it. The runner
records actual CLI/module import paths and distribution provenance; changing the
CLI or dependencies creates a new qualification, not an extension of an old one.

Inside the owned DTU, from the candidate checkout:

```sh
python scripts/qualify_status_emitter.py \
  --owned-dtu dtu-unified-codex-dev-20261014 \
  --python /owned/native-env/bin/python \
  --cli-python /owned/cli-env/bin/python \
  --unified-wheel /owned/wheels/amplifier_unified-CANDIDATE-py3-none-any.whl \
  --status-wheel /owned/wheels/amplifier_status_emitter-0.1.0-py3-none-any.whl \
  --core-wheel /owned/wheels/EXACT_PUBLISHED_CORE_PLATFORM_WHEEL.whl \
  --work /owned/status-acceptance-new-directory
```

The hostname must match `--owned-dtu`; work must be new and outside source.
No current serving environment is installed into or uninstalled from. The runner
creates fresh standalone-consumer and package-ownership environments, runs the
real installed CLI plus Unified worker with local synthetic work, and then runs
the copied package probe outside source. Only the disposable package environment
uninstalls the standalone wheel. Both wheels must match the reviewed source;
the source digest must remain unchanged during execution. `result.json` is true
only after both probes pass. Retain that receipt, command logs, wheel hashes and
dependency identities, then audit/remove the work directory. No live model,
native Windows, TUI, recipe-specific or ACP acceptance is claimed.

### Shared Work native and nested-effect ownership (October 22)

The existing automatic `native` owner adds the complete
`tests/test_work_origin.py` and `tests/test_work_native_effects.py` modules.
Their two `additionalRecords` and narrow Work implementation/fixture path rules
preserve the frozen 87-record baseline and all earlier owners. Missing native
adapter APIs, loop prerequisites, JavaScript runtime or skipped cases fail the
owner; an import alone does not qualify those boundaries.

The tests use controlled provider transport with actual worker-origin handling,
native callbacks, nested JavaScript dispatch and host effect checks. The reused
parent case requires explicit `work_coverage_unavailable` before ordinary receipt
deduplication; unchanged storage alone is insufficient. Ordinary API suites
`test_workspace_work.py`, `test_work_invocation.py` and `test_work_effects.py`
retain their Full Python owner. The existing single `shared-work` extended-browser
scenario retains cold-task coverage; source-browser and installed-wheel evidence
remain separate.

Prior checkpoint `6ac9611ddd85205e2781fe2e17f3bc950feee030` identifies historical
evidence with its original exact-input reuse limits, not the current integration.
This additive registration and the new integration remain pending qualification;
tests for these registration changes are **UNRUN** until owned isolated execution.
No new workflow, broad gate, paid-provider claim or whole-feature acceptance is
added. Generic streaming, unmeasured steering and tool-internal awaits remain
outside that prior qualification.
