# Waiting app update qualification

The ordinary update owner keeps checking the published release channel while a
validated app waits for active work. A manual check only refreshes availability;
the existing installation intent qualifies a strictly newer candidate separately.
Ordinary idle activation uses the newest known validated target. Explicit force
restart still consumes the exact confirmed candidate, whose version is now shown
in the confirmation even when availability lists a newer release.

Qualification ran in the project-owned Linux ARM64 DTU with synthetic state.
No live host was updated or restarted and no provider turn was sent.

| Boundary | Result |
| --- | --- |
| Released-main control (`0df2c57f5`) | The new busy-host test fails because a pending app prevents any release check (`checks == 0`). |
| Held runtime-lifecycle lock control | The earlier draft promotes the old candidate after a concurrent check discovers the newer release. The final admission guard prevents that promotion. |
| Pending app, app updates, explicit restart tests | 76 passed; actual staging/activation owners with synthetic installer, graph, promotion and restart boundaries. |
| Updates, overview and restart renderer tests | 56 passed, including exact prepared-version text and unchanged authority payload, stale confirmation rejection and read-only refresh. |
| Browser ownership contracts | 165 passed; existing `update-effects` journey gains a maintained extended owner. Original 65 scenarios are unchanged; source-script union is 139. |
| Version, notes, standalone contracts | 56 passed for 0.20.184. |
| Built-client update journey | Passed; newer/offline checks preserve the original qualified receipt, generation, history and draft. Unknown install still refuses retry; known failure requires explicit retry. |
| Installed 0.20.184 wheel | Canonical isolated app probe passes, including assets, PAM availability and separate worker imports. Only the candidate distribution changes in the copied dependency graph. |
| Installed built-client journey | Passed in 6.426s, zero remaining or force-retired processes. The unchanged fixture/test run outside the source tree with no Python app package beside them; the server serves the installed wheel. |

Build identity: `2486a46908ac74f1`, version `0.20.184`. Wheel and source archive
pass `scripts/release.py verify`; packaged source and client bytes are checked.
The focused browser owner uses `tests/web/update-effects-browser.mjs` and
`tests/fixtures/update_effects_server.py`. Its installer/rollback effects remain
synthetic; Python tests separately exercise the canonical stage/activate owners.

New Python coverage is in `tests/test_pending_app_updates.py` (also collected by
the existing full Python owner). It covers periodic and idle checks, retained
install intent when automatic installation is disabled, manual-read separation,
held checks and lifecycle locks, failed newer validation and explicit retry,
offline fallback, older/moved/invalid release metadata, and feature-selection,
started-handoff and force-confirmation exclusions.

Retained local evidence is under `output/update-latest-20261019`: original failed
controls, source/build/package identities, complete focused logs, browser receipts
and screenshot. Setup failures are retained separately: a copied Vite launcher
required its private relative link, the older browser fixture lacked a release
label required by attention projection, and the isolated installed-test tree
needed the Settings label helper. No failed result is relabeled as passing.

Limits: checks cannot know about a release published after their lookup; a recent
lookup is reused for up to a minute before ordinary idle adoption. Offline use may
adopt the already qualified release. Failed newer validation stops automatic
adoption rather than silently falling back. Superseded app staging directories
remain retained because the existing automatic cleanup owner only covers
ecosystem generations. This is not live-provider, physical-device, actual network
release-download, or spark-1 adoption acceptance.
