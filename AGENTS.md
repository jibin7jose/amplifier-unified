# Contributor operating guidance

For source discovery, use the [source and ownership map](docs/source-context.md).
`python scripts/source_context.py` summarizes tracked inputs without reading
their bodies; select relevant owners, contracts and tests before expanding context.

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
