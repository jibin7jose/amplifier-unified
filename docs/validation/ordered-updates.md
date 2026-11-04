# Ordered in-app updates

Updates check the published app first. An available release stops component
inventory/checks until installation and verified restart; the new process reads
its own shipped dependency declarations. A failed app check also defers component
checks rather than assuming the installed app is current.

Next are included components: the declared worker dependencies and Git sources
in shipped app behaviors. Missing source caches or an uninitialized worker lock
are installation candidates even when no existing cached row exists. Local
source overrides, pins and protected edits retain the existing update policy.
After included updates activate, the updater inventories and checks remaining
sources against the new generation. Tiers with no updates are skipped.

Check now remains read-only. Install remembers intent for the whole sequence,
including across an app restart, without changing the user's automatic-install
setting. Automatic installation uses the same sequence. Installation errors stop
progress; work and calls must be idle for activation. A fresh process must prove
its installation before component checks continue. Rollback cancels continuation
and retains the existing rule that automatic installation is disabled.

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

Validation covers app-first early return, empty-tier progression, included-check
failures, explicit install intent after a verified restart, missing-source
revision validation, read-only inventory, preserved source policies, and a
real two-repository staged update. In that staged test, active work holds the
first activation; after idle, included and optional source copies advance in
order while both original repositories remain unchanged.
