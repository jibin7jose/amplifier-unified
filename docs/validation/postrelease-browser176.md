# Post-release browser reload qualification (.176)

Published source `b21dfe0619537df4590f275a6228cc384a09ff1c`, post-release run
[39061951410](https://github.com/microsoft/amplifier-unified/actions/runs/39061951410),
extended-browser job `117481595915`, failed two of its 88 scenarios:
`canonical-work` at its final reload receipt check and `canvas-media` at its
post-reload playback check. Their failed logs remain retained unchanged.

## Canvas playback

Both original full scenarios first passed unchanged in the owned Linux DTU.
A diagnostic sequence of actual Canvas reloads then reproduced the playback
assertion: all four data/blob audio/video elements decoded successfully, but
remained paused at time zero. The document's button status remained “Ready to
review.” No click reached the iframe in that observed failure.

A controlled unchanged-product reproduction held the new iframe's real pending
render report during reload. The copied client view still displayed the previous
ready label. Releasing the pending report between pointer down and up moved the
Play button upward by 27.5 px: pointer down hit `BUTTON#play`, while pointer up
and click hit `BODY`. The original playback assertion failed. This proves that
waiting for the inherited label alone is insufficient; the hosted failure did
not retain enough frame diagnostics to identify its precise event ordering.

The fixture now observes the new page's actual accepted ready-report response,
then its public revision/client and ready state, before the existing ordinary
Play click. The same delayed-report control verifies that decoded media and the
old ready label cannot prematurely authorize that click. After releasing the
report, the complete original decode/play/pause/seek/reload, opaque-parent and
blocked-network assertions pass. No application code, built assets, media,
assertion or timeout was changed.

## Canonical Work remains unexplained

The unchanged full journey passed, as did a diagnostic sequence of 15 additional
reloads and the final journey with improved diagnostics. These passes do not
explain or erase the original 15-second timeout. No Work projection behavior,
receipt assertion or retry policy was changed. The scenario now retains its
existing screenshot and group receipt under the uploaded scenario directory;
its bounded failure record also distinguishes the public receipt from the
rendered receipt and records reading-window controls. This remains an
unreproduced post-release test failure requiring its next failing receipt.

## Owner and evidence

Both scenarios remain in `tests/web/browser-checks.json`, owned by the
extended-browser post-release/manual lane. Case-specific artifact destinations
are within the existing uploaded `output/browser-checks/` tree. The 88-scenario
count and original 65-command migration fingerprint remain unchanged.

Task evidence is retained under `output/release176/browser-correction/`:
unchanged run, reload diagnostics, deterministic pointer-boundary failure,
delayed-report correction control, final full scenarios, registry JUnit, file
hashes and cleanup receipt. All browser runs use the unchanged published .176
client in the owned DTU and synthetic state. They establish browser playback,
not physical audio/device or live-provider acceptance. This fixture-only change
does not require another application release.
