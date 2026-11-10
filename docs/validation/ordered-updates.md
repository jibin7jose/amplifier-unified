# Ordered in-app updates

Updates check the published app first. An available release stops component
inventory/checks until installation and verified restart; the new process reads
its own shipped dependency declarations. A failed app check also defers component
checks rather than assuming the installed app is current.

Next are included components: the declared worker dependencies and the effective
enabled app behaviors. App behavior selection uses the same registration, local
path, source-override and exclusion rules as conversation preparation, without
loading or composing bundles during a check. Only the selected repository and
ref belong to this tier; a different cached ref or an unselected sibling does
not become included because its repository URL matches. Shipped behavior
declarations are read only when that behavior remains selected.

Missing source caches or an uninitialized worker lock are installation candidates
even when no existing cached row exists. An installed package does not replace
the declaring bundle's source copy: both checked rows participate in the same
included batch, whose source copies are staged before dependency collection and
qualification. Local source overrides, pins and protected edits retain the
existing update policy.
After included updates activate, the updater inventories and checks remaining
sources against the new generation. Tiers with no updates are skipped.

The inventory records a bounded witness of the effective app configuration and
selected references. Selection changes invalidate the inventory index and refuse
staging from stale checked rows. Installation verifies both the current selection
and the copied candidate configuration before using that batch. This witness is
not installation authority: source identity, dirty-tree protection, authored
dependency constraints, exact checked revisions and offered-profile qualification
still apply. An unresolvable or ambiguous app selection requires correction
rather than substitution with a default branch. Discovery reads configuration
and selected shipped declarations, not conversation history or arbitrary cached
bundle contents.

Before installation, checked Git identities must agree with both explicit batch
roots and the reachable dependencies evidenced by the canonical source collector.
A selected dependency with unresolved provenance cannot be bypassed merely
because it is not itself an explicit installer root. Unrelated installed extras
are not selection evidence. After alignment, the same guard requires exact
checked revisions before the dependency transaction.
An evidenced transitive full-SHA pin may be retained when its authored,
installed and checked commits, repository and subdirectory agree exactly.
That retention evidence grants no moving-reference recovery or permission to
advance the pin, even when the checked installed policy names a branch.

Check now remains read-only. Install remembers intent for the whole sequence,
including across an app restart, without changing the user's automatic-install
setting. Automatic installation uses the same sequence. Installation errors stop
progress; work and calls must be idle for activation. A fresh process must prove
its installation before component checks continue. Rollback cancels continuation
and retains the existing rule that automatic installation is disabled.

A separately verified manual Windows handoff can complete that sequence's app
leg without inventing a successful automatic restart. Its identity-bound receipt
continues the ordinary included/other component stages with only the saved Install
intent; verification without that intent installs nothing. A newer published app
remains visible but does not retarget this unfinished component leg. A later fresh
check starts the normal app-first cycle. Reload revalidates the actual installation;
copied receipts, changed dependencies and later conflicting intent do not inherit
the earlier proof. Native installation qualification and fixture-based scheduling
checks are distinct evidence, not a claim of live adoption.

A failed optional Smart Tool update retains its current installation, failure
receipt and explicit Retry/Skip controls. It must not block checking or staging
a newer application, or silently retry when the application sequence resumes.
Unfinished ordinary tool activation still owns its existing admission gate;
independent runner qualification keeps its own owner. Later checks exclude
retained failed installations until explicit recovery, and unrelated healthy
tools may proceed. `tests/test_failed_tool_update_independence.py` covers these
boundaries, including restart retention and preservation of foreground app state.

Native child/job activity is bound to the runtime process that reported its
lifecycle. After that exact process exits, unfinished owned rows become
interrupted, including persistent children that were idle. An early closed
event or a Stop request alone is insufficient. This releases obsolete update
blockers without claiming success, replaying work, discarding saved reports,
or settling independent peer chats, replacement processes or unbound rows.
`tests/test_runtime_worker_exit.py` exercises the real process-exit boundary,
ordinary update readiness and coordination observations, including those fences.

The application status box summarizes included and other component availability,
missing installations, unresolved checks and waiting phases. Detailed source
inventory remains below the box. Frontend and agent callers share updates.check,
updates.install, and the sequence/summary state.

Verified restart readiness retains one application result independently of the
bounded diagnostic history and later component attempts. The Updates page shows
that result only for the same running version/revision and without an unresolved
application replacement or restart. Current-installation reconciliation is
labeled verification, not a successful historical restart. A later failed
component batch names its bounded component list separately and asks for a fresh
check when sources moved. Old attempts and newer app-check failures cannot inherit
the component-failure summary. This is presentation of confirmed outcomes; it does
not change installation, retry, rollback, or restart admission.

The maintained `update-progress` browser smoke covers app-success/component-failure
separation, reload, narrow layout, stale app identity, and unconfirmed restart,
alongside its existing progress/preferences/no-install-effects checks. Readiness
tests cover actual result creation and service reload after diagnostic retention
has advanced beyond the app batch.

`tests/test_update_dependency_recovery.py` covers component source failures whose
package is a transitive dependency of an updated bundle. It checks activation
and reload against the existing active pointer, qualified worker generation,
installed graph and complete batch scope, including a sanitized observed receipt.
More than five components retain full recovery scope while the UI keeps its
bounded preview. Unrelated generations, data-only reuse, missing qualification,
legacy truncated scope and newer failure occurrences cannot establish recovery.
Original failure evidence remains in the existing journal after settlement.

Application storage failures can also recover when a newer release with the
same known optional features completes staging and verified restart. Capture the
prior occurrence before staging and preserve it across activation/restart;
checking, partial preparation, listener-only reconciliation, changed or unknown
features, and later failure occurrences do not clear it. Preserve the original
failure in update history. Target-specific non-storage failures keep their
existing recovery requirements. When the outgoing updater predates this snapshot,
the same attempt's retained stage-start and success receipts must prove matching
revision/features and an earlier exact failure occurrence; incomplete or ambiguous
receipts do not establish recovery.

Validation covers app-first early return, empty-tier progression, included-check
failures, explicit install intent after a verified restart, missing-source
revision validation, read-only inventory, preserved source policies, and a
real two-repository staged update. In that staged test, active work holds the
first activation; after idle, included and optional source copies advance in
order while both original repositories remain unchanged.

`tests/test_effective_behavior_updates.py` exercises passive selection, exact-ref
tiering, real registry-resolution parity, stale selection and bounded discovery.
The opt-in `tests/test_effective_behavior_update_native.py` exercises public
`UpdateManager.check` and `install` with real source collection, dependency
installation, freeze and offered-profile mounts. Run it only in the prepared
`python-project` managed environment with
`AMPLIFIER_EFFECTIVE_UPDATE_NATIVE=1`. It uses no vendor/model calls.

Supply Git bundles at `evidence/foundation-transition/{foundation,routing}.bundle`
containing the exact old/new revisions named in the test. The fixture transports
Git fetches to local mirrors while retaining public source identities; its
synthetic routing `main` is candidate evidence until an accepted public merge is
verified. It repacks installed test-image registry payloads and declares a
fixture Core Git source; it does not qualify public registry transport or Core
publication. Preserve those limits, the raw prior failures, and the source/run
identities with any reported result. These affected checks are not a new routine
release gate or permission to refresh installed user components.

For accepted-public-source follow-up, the manual `python-checks.yml` suite
`updater-public-selected-source` reuses the existing hosted omitted-ref lane.
It is not selected by full, nightly, PR, or release qualification. Its fixed
adapter runs the ordinary manager against synthetic user state, the unchanged
shipped worker manifest, official registry artifacts and actual public source
heads. Routing and Foundation revision assertions fail on drift rather than
substituting a synthetic branch. The existing lock-exact private Loop checkout
is the sole transport exception and is reported separately; this is not an
exclusively public graph. No provider account or inference request is supplied.

Retain the candidate/harness identity, resolved and installed graphs, source
policy, profile qualification, rollback and bounded JUnit outcomes. Raw logs and
private source remain on the disposable runner; export their hashes and fixed
failure categories, never private source excerpts. Successful cleanup is reported
separately from test success. A failed or unknown managed guest check is not
replayed by this distinct hosted qualification.
