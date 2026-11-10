# Settings browser isolation and unsaved-state coverage

The settings, settings-completeness and settings-drafts journeys start their own
synthetic fixture on an operating-system-selected loopback port. Readiness comes
from that child process's PID and address, after the application has started.
An unrelated listener's health response cannot satisfy fixture readiness.

The October 17 full smoke run exposed the previous fixed-port problem: a retained
preview already occupied port 8957, the new child failed to bind, and the health
probe accepted the retained preview. The settings test reached its sign-in page.
Its diagnostic handler then obscured that original failure with a missing view
field. Failure screenshots and state diagnostics are now best effort; the actual
assertion is always reported and rethrown. Product authentication and assertions
remain unchanged.

`settings-drafts` is an explicit additive scenario in `browser-checks.json`.
It covers unsaved provider input across Close, agent-requested dismissal, review
and save, narrow-screen browser Back, cancel/discard and model-picker management.
The original 65-scenario migration hash stays frozen. The source ownership union
grows from 125 to 126 scripts without duplicating another lane.

Desired outcomes: two concurrent fixture starts have different child identities
and listener addresses; the existing preview remains untouched; the three
journeys pass with private synthetic state and no browser errors. Execution
receipts record the actual source, ports and results separately. This headless
acceptance does not qualify physical device or installed-PWA behavior.

## Awaited fixture shutdown

The first 211-case census reported a passing settings-drafts journey followed by
four processes terminated by the external owner. That receipt recorded the count,
not the process identities, so it does not establish whether those were server
or browser descendants. It remains a failed cleanup result.

The fixture previously had no SIGTERM handler and its callers did not await child
exit. On POSIX it now handles SIGTERM/SIGINT by unwinding AppRunner cleanup and its
private temporary directory. All three journeys await browser closure and then
server closure, including when browser closure fails. Shutdown is bounded at ten
seconds; forced reaping after that bound is still a failure. Existing assertion
errors remain the primary error, with cleanup errors reported alongside them.
No UI assertion or outer process-owner requirement changes.

`node --test tests/web/fixture-lifecycle.test.mjs` owns the child lifecycle,
forced-cleanup failure, original-error preservation and browser-close failure
controls. `pytest tests/test_settings_fixture_lifecycle.py` owns real POSIX
SIGTERM/SIGINT cleanup of the application's private home and listener. These are
ordinary frontend/backend suite inputs; their Windows signal cases are explicitly
unsupported, not evidence of Windows graceful shutdown. The three full browser
journeys additionally require the external owner to observe zero residual
processes without termination. Record their exact process identities on failure.

The same census found storage-history UI assertions passing with two owner-retired
processes, again without captured identities. Its synthetic server had the same
missing signal handler, and its browser sent an unawaited termination. The shared
`fixture-lifecycle.mjs` helper now also covers that journey, closing browser and
Vite independently before awaiting the server. A failed closer cannot bypass the
remaining cleanup. Its real server signal cases join the focused Python test; the
full storage assertions remain unchanged. The original two-process result stays
a cleanup failure until a corrected run proves zero residuals.

Qualified in the owned ARM DTU on October 17: the unchanged settings fixture
fails the real SIGTERM control (`-15` instead of normal exit). The corrected
source passes five Node lifecycle controls and four real server signal cases.
Settings, settings-completeness (240 layouts), settings-drafts, storage-history,
and both existing standard storage consumers (four input variants each) pass.
The unchanged external owner reports zero terminated or remaining processes for
each successful case; a final process/reference audit is empty and retained
preview 8957 has the same PID/start identity. The first attempts at the two
additional consumers stopped in the screenshot adapter before app startup
because those IDs were not allowlisted. They use owned environment-directed
screenshots, so only that unnecessary adapter was disabled for their subsequent
successful runs; the original setup failures remain recorded.

Evidence is retained at
`output/afk-approved-20261017/settings-lifecycle832/qualification.json` with
original and corrected receipts, tested hashes and cleanup identities. The
original census's unidentified four/two residual processes remain unclassified;
these focused proofs establish corrected behavior without inventing their cause.
