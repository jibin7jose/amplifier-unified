# Contributor operating guidance

Before broad source reads or searches, use the [source and ownership map](docs/source-context.md).
`python scripts/source_context.py` summarizes tracked inputs without reading
their bodies; use `--format paths` with `--under` to select relevant owners,
contracts and tests. Ordinary `rg` does not apply these context classifications;
use the map's selective-search examples, and expand to retained evidence or
generated output when the task needs it.

## Assess scope and design before taking on issues or PRs

User direction, October 13, 2026: an issue or PR is a proposal, not an automatic
commitment. Before implementing, sponsoring an upstream change, or calling it a
release prerequisite, verify that it affects Amplifier Unified's actual runtime
and dependencies. Shared naming is not evidence of dependency. In particular,
Amplifier Agent is separate from Unified's Core/Foundation runtime.

- Check the problem against `docs/architecture/VISION.md`, the applicable client
  vision, and the relevant contracts. Identify the canonical owner and evidence.
- For direction or contract changes, use [vision and contract guidance](docs/vision-and-contracts.md):
  keep observable promises separate from design notes and implementation evidence.
- Confirmed defects in promised behavior normally warrant straightforward fixes.
- New features and behavior or architecture changes require judgment about whether
  they are desirable, their general client needs, and the right design; do not
  accept the issue's proposed implementation by default.
- Any intentional change in direction must update the governing vision/contracts
  explicitly as part of the proposal before implementing the divergent behavior.
- Keep unrelated upstream improvements separate from Unified release gates.
  Close out-of-scope tracking and correct misleading reviews when discovered.

## Make performance changes reviewable

For changes to repeated reads, publication, persistence, runtime preparation or
provider requests/cache reporting,
follow [performance validation](docs/validation/performance.md). Identify the
canonical owner, triggering operation and inputs that grow before choosing an
optimization. Review work done before filtering or sending a response, including
history reads, copies, scans and encoding. Retain a focused regression check for
the demonstrated cost and the affected history, authority and recovery semantics.
Use measured workload budgets; this does not add a full-suite release gate.

## Keep the development release loop fast

Every release needs an authored What's new entry, including fixes-only and
maintenance releases. Before choosing a version or preparing a release PR,
follow [version choice and release notes](docs/validation/release-qualification.md#version-choice-and-release-notes).
The release owner checks the shipped changes and high-impact guidance; a version
bump and passing format validator alone are not a complete release summary.

User direction, November 4, 2026: the **total routine per-release loop targets
five minutes or less**, including publication and the published-package smoke;
moving an hour of work into the background for every release is not acceptable.
Reuse maintained release commands and exact-input PR/DTU evidence; do not recreate
one-off qualification harnesses or full-tree evidence packages for each version-only
release. Fast keeps identity/notes and asset/build smoke for proven version-only
PRs, avoiding unchanged release/reporting/access harness tests; changed or unknown
inputs retain full checks, with one full nightly/manual owner. Do not relabel smoke as
full-suite evidence. Run affected checks when inputs
change, with specific checks for material risks such as migrations or authority
changes. Do not repeat full suites for version-only preparation.

Run package/frontend/native-runtime qualification together on one read-only runner,
then publish on an isolated write-enabled runner. Keep one published-asset
checksum/install probe; successful automatic smoke finishes on its read-only
runner without another reporting queue. Broad Python, readiness, browser and
integration coverage runs **once nightly around 01:00 America/Los_Angeles**, or by
explicit manual investigation. Do not launch broad campaigns or model-powered
monitor sessions for every release. The scheduled workflow owns reporting;
assign human/agent follow-up only for actionable failures. Remove duplicated
coverage only when another owner exercises the same boundary; preserve distinct
installed-package, platform and media behavior.

This is a measured target, not permission to suppress failures, cancel slow checks
into success, change branch protection, or relabel old evidence. Report queue,
cold-build and required-check overruns separately and fix their causes. Published,
smoke-verified and broadly verified are distinct. Preserve failures and immutable
release bytes; a smoke pass cannot close a broad-suite failure. Do not expand the
routine graph without an explicit decision and an update to
[the release policy](docs/validation/release-qualification.md).

## Bundled client and server evolve together

User direction, October 14, 2026: the web client ships with the server. Change
bundled interfaces together and remove superseded bundled paths; do not add
old-bundle compatibility fallbacks or capability negotiation. Independently
released clients, custom components, and explicit public APIs have their own
contracts and version policies. Preserve or deliberately revise those contracts
with their owners instead of silently changing their promised behavior.

## Interactive archive boundary

Keep archive feedback/navigation in the bundled interactive controls, not the
public `session.archive` action. Qualify late-response navigation races, the
noncurrent-chat path, Undo, and the raw action's selection-preserving behavior.
Release the interactive mutation lock on its confirmed receipt, independently of
navigation. Qualify stalled navigation with usable Undo and superseding intent.

## Native Windows work

Native Windows backend support is not yet qualified. Follow the
[setup design](docs/design/native-windows-installation.md) and keep WSL evidence
separate. Never use Windows `os.kill(pid, 0)` for passive liveness; it can terminate
the process. `tests/test_generation_leases.py` includes an unmocked sentinel.
The focused platform workflow selects passive liveness, shared/exclusive repair
locks, bounded UTF-8 worker pipes, private files, owned-process containment and
offline installer fixtures, not full Windows installation. Windows preflight
requires ordinary x64 CPython 3.13 and both PowerShell 5.1 and 7. A separate
focused job checks private configuration, TLS, read-only TokenUser identity and
service policy, plus the real CLI-to-service root binding through a fake native
transport. It uses pytest, cryptography, PyYAML, filelock and binary-only aiohttp;
it explicitly excludes synthetic Task Scheduler registration. Inspect named native
cases and skip reasons in the candidate's CI logs; partial passes do not qualify the host.
Keep its
worker subprocess probes isolated with `-I -S`: installed application packages
must not hide import dependencies in this standard-library check. A cancelled
Windows stdin read stops delivery; its daemon thread/descriptor may remain until
EOF or worker-process exit. Do not join it or close its buffered stream from the
event loop.

Author login-page HTML and standalone scripts in `frontend/public/`, not only
`amplifier_web/static/`. Vite replaces the generated static directory during
builds. Verify canonical/generated byte parity and the served routes after
rebuilding; a source-only login check does not prove packaged browser entry.

The foreground owner-browser reader uses the existing-only native private
credential boundary after qualified cold startup. Qualify the exact installed
public entry and real foreground CLI before presenting a usable trial; source
clearance alone is not acceptance. Keep installer, default/background launcher
and Task Scheduler activation closed pending their separate native acceptance.
Launcher HTTP health JSON is not listener authentication: require the private
local leaf pin and HTTPS before reading or sending a control credential. The standalone process
guard supports ordinary CPython 3.13 only and requires explicit
`sys._base_executable`; do not weaken PID checks to accept a venv redirector.
Task Scheduler expands percent-bearing executable/working-directory paths.
Reject those paths rather than recording a literal identity that launches a
different program. Task registration/state is not authenticated host health.

## Keep agent context and coordination useful

Workspace starter instructions and the coordinate-work skill must support one
manager coordinating many independent workers through complete deliverables.
Prefer current task briefs, exact outcome links and on-demand details over
repeated whole-history reads or routine progress/approval exchanges. Preserve
actual Stop, authority, ownership and unknown-effect recovery boundaries.

Automatically loaded scratch is a short current context/index, not task status,
a release ledger, event history or new authority. Keep detailed evidence with its
owner; replace stale current entries and preserve useful history outside automatic
includes. Changes to these templates should verify rendered prompts omit linked
archives while current notes still reload, and preserve existing user files.

Apply the [context contract](docs/workspaces/contracts/context.v1.md): keep the
always-on host guide short, put specialist recipes behind existing skill/schema
discovery, and retire superseded workarounds with their code. Reuse repository
guidance until its relevant source or scope changes. Validate actual composed
requests and installed companion discovery; fewer bytes alone do not establish
better model behavior or authorize removing user-selected capabilities.
