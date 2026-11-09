# Prebuilt releases without generated files in Git

Status: implementation proposal, November 9, 2026. Source review uses
`c184826828c27c058edbf4a1d03f0ec4cc03961c` (0.68.5). This proposal changes no
installed application, release requirement or source-selection policy.

## Intended result

Keep authored frontend inputs in Git and ship the complete built UI in both
published Python distributions. Normal installation, updating and rebuilding a
published source distribution into a wheel need no Node or frontend download.
Building the UI from a raw Git checkout requires Node and locked frontend
dependencies. Backend-only iteration can reuse an existing built UI.

This removes generated review/context churn without splitting the currently
bundled client/server contract. Authored `frontend/public` files, upstream inputs
and licenses remain source; published artifacts remain immutable release output.
The [family vision](../architecture/VISION.md), [update contract](../UPDATES.md)
and [focused release policy](../validation/release-qualification.md) retain their
authority.

## Why deletion must follow the updater change

The initial installer downloads a published wheel, but `app_updates.stage` and
the older-bootstrap replacement path still install `git+SOURCE@revision`.
Removing `amplifier_web/static` would leave that active update path without the
UI. Optional-feature installation uses the same installer and exact app revision.

The dependency is wider than an install URL: `app_component_graph` currently
accepts Git provenance for direct dependencies, `app_features` requires that
source identity, and `update_readiness.running_identity` obtains the revision
only from installed Git metadata. Generation promotion, replacement qualification
and restart acknowledgement rely on those identities. Treating a wheel as an
unidentified package would lose existing recovery guarantees.

## One source identity, explicit artifact delivery

Keep repository, full source commit and package version as the application source
identity. Record delivery URL and SHA-256 separately in the existing component
graph/qualification receipt. An archive is not a Git checkout, and its hash is
not a source revision. Preserve existing Git receipts for already installed and
staged generations; do not rewrite historical evidence.

The release build should embed a small source-identity record in the package.
The release owner derives it from the selected clean candidate, checks it against
the existing candidate receipt, and includes identical bytes in wheel and sdist.
A wheel rebuilt from the published sdist keeps that record without Git or Node.
Raw source builds cannot claim to be a verified published artifact merely by
writing the same record. Runtime identity must also match the serving package and
its observed installation provenance; desired update state is not proof.

The current first installer verifies a downloaded wheel and installs it from a
local path, so its `direct_url.json` need not contain the remote release URL.
Qualification must cover this supported first-install path as well as uv's remote
archive provenance. A wheel rebuilt from a published sdist retains the source
identity but has its own delivery bytes; define its update/feature eligibility
explicitly rather than equating its hash with the published wheel. Neither case
justifies accepting arbitrary local sources or a source stamp alone as proof.

Use the existing installer and resolver. Preserve explicit verification of the
downloaded wheel before installation; a hash in a URL is not sufficient evidence
that the selected installer checks it. Do not introduce another download cache,
application registry or activation owner.
Resolve the selected release tag to its exact source commit and bind the official
asset name, version, URL and digest before staging. Keep the artifact identity
with the qualified generation so activation and recovery do not select a newer
asset or silently fall back to source installation.

Extend the existing graph/identity validation only for this supported archive
case. Keep custom, editable and explicitly selected sources under their existing
owners. Feature installation must preserve the same app and every existing
component; it must not turn a custom source into the packaged default. Existing
Git candidates may finish under their recorded contract. Keep cancellation,
uncertain replacement, idle admission, generation promotion and restart
acknowledgement with their current owners.
For a same-app feature addition, resolve the artifact for the serving app's exact
release identity, not the latest available release, and preserve its baseline
components and transitive extras through the existing qualification checks.

## Reviewable delivery sequence

### Observed installer limitation

An isolated Linux ARM64 check of uv 0.13.6 rejected the initial assumption above:
ordinary `uv tool install` accepted a deliberately incorrect SHA-256 URL fragment
with a fresh tool directory and cache. Its installed `direct_url.json` retained
the URL but an empty `archive_info`. `uv pip install` rejected the incorrect
fragment, but also omitted the installed archive hash, including for the official
0.68.5 wheel. That real-package check used no dependencies or application startup;
it establishes metadata behavior, not a qualified application update.

The [tagged tool implementation](https://github.com/astral-sh/uv/blob/0.13.6/crates/uv-tool-commands/src/install.rs#L1026)
uses the default hash strategy on its ordinary path. Experimental tool locks and
the separate pip hash-verification path do not establish the needed maintained
tool-install behavior.

Keep the current verified local-wheel installation until A/B has a qualified
delivery/provenance design. Do not fabricate installed hash metadata, infer it
from the source stamp, or add a parallel installation registry. The source record
can ship independently; graph schema, archive feature eligibility and readiness
must follow the supported delivery path. Preserve exact existing Git receipts.

### Batches

| Batch | Change | Evidence before the next batch |
| --- | --- | --- |
| A: artifact identity | Embed and verify release source identity; represent supported archive delivery in the existing app graph/readiness owners | Real wheel and sdist build, Node-free sdist-to-wheel rebuild, installed provenance and exact revision checks; malformed/mismatched identities rejected; existing Git receipts remain readable |
| B: published-package updater | Stage verified release wheels through uv; carry their identity through feature additions, older-bootstrap replacement, generation promotion and recovery | Owned DTU update journey with no Node; same-app feature preservation; interrupted staging/replacement, restart acknowledgement and rollback-source checks; no replay or adoption of a different app |
| C: source-only Git tree | Explicitly package staged ignored static output; remove tracked generated assets; update canonical build, proof reuse, artifact application and active CI consumers | Fresh Git build, clear missing-UI packaging failure, complete wheel/sdist byte parity, Node-free rebuild, installed browser entry and affected login/PWA/vendor checks; measure routine release cost |

Each batch can be reviewed independently. A and B retain generated static in Git.
They may proceed while C remains held for an old-client transition decision and
qualification. Reliable ordinary in-app updating remains the consumer goal.
Unrelated releases continue normally. Release publication and live adoption remain
separate operations owned by the release/deployment operator.

### Older updaters can skip the bridge release

Shipping B alone does not make C safe: an older running updater selects the latest
release and could jump directly to a source-only revision using its old Git path.
Before C, decide and qualify a supported user-visible transition. Adopting the
bridge through the current updater or requiring a one-time terminal/package
installer upgrade are options for that later decision, not approved defaults.
In particular, this proposal does not authorize replacing ordinary in-app updating
with a required installer step. Any selected transition must explain and qualify
what older clients encounter; a missing UI must not become an apparently successful
installation.

C remains held until the supported transition is decided and qualified, including
any requirement for uninterrupted updating from older supported releases.
A new minimum-updater field cannot change already shipped code that ignores it.
Spark-1 adoption of the bridge is a separately authorized deployment, not a
development test or sufficient evidence about all other installations.

## Build and qualification ownership

The canonical frontend build continues to produce `amplifier_web/static`.
Hatch packaging must explicitly include that ignored output in wheel and sdist;
editable development setup must remain possible before the first frontend build.
Published archives include all generated assets, licenses and browser entrypoints.

Prefer building once on the existing read-only release runner, then using exact
PR evidence to avoid repeating unchanged frontend tests. Proof lookup must bind
the newly staged asset bytes as well as source/toolchain inputs. Reusing test
evidence never supplies missing UI bytes. Measure dependency preparation and build
time within the existing release budget before adding artifact-transfer machinery.

`frontend_build_artifact.apply` needs an explicit empty-destination path before
generated output is ignored. Preserve recovery and local-work checks when a
destination exists. Replace active committed-static parity checks with staged
build/package checks; keep historical frozen-revision comparisons intact.
Review platform/bootstrap jobs that currently depend on checked-in login/PWA
assets, including their path filters and actual setup order.

Concrete consumers include `scripts/cold_restore_checks.py`, which currently
derives its nonempty asset inventory from `git ls-files`; it needs a separately
bound generated-output inventory. Fast and general browser checks currently run
asset-consuming tests before the build, while the full Python and two Windows
installed-package jobs rely on static output arriving with the checkout. Stage
output before those consumers, preserve early failure/deadline evidence, and
keep unrelated source-only checks narrow. Tests of shipped bootstrap bytes must
continue inspecting built or installed files rather than only authored inputs.

This proposal does not claim the updater transition, static deletion or installed
journeys have been implemented or qualified. Retire the temporary transition
instructions after the supported old-updater window closes; keep lasting source,
package and recovery promises with their existing contract owners.
