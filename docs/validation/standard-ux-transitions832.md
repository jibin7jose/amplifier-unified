# Saved-view and restored-navigation test boundaries

The full source browser census on `9e5db0d22494a873569a1fec09d65dd292d9737e`
exposed two fixture transition assumptions. The product code and packaged client
are unchanged by these corrections.

## Model settings close before reload

The composer closes its popover optimistically while saving the client view.
The old connection-inspection test clicked Close and immediately reloaded. A
separate diagnostic held that exact `view.update` request before dispatch: the
local view was closed, while the authoritative saved view remained open. Reload
then restored the open popover, and the original attempt to click its covered
trigger failed with the same pointer interception as the census.

The routine fixture now waits for the exact close command's accepted response,
then checks the closed popover, trigger and public view before reloading. After
reload it verifies the closed state and uses the original ordinary trigger. It
still requires high reasoning effort to survive reload, hides unsupported effort
controls, retains the private draft, and observes no chat creation, runtime start
or sent input. No timeout was increased and no click is forced.

The diagnostic establishes a sufficient cause on the unchanged client. The
original census retained no request-order trace, so its exact interleaving is not
claimed. The held-request diagnostic remains evidence, not a routine dependency.

## Navigation restored after Settings

On a narrow screen, opening Settings temporarily covers the navigation drawer
used to reach it. Closing Settings restores the drawer's existing expanded state.
The old decisions test treated the presence of the underlying Open navigation
button as proof the drawer was closed, so the restored drawer intercepted it.

The touch fixture now verifies the restored drawer, uses its ordinary Close
navigation control, and awaits hidden UI plus the closed public view. It then
exercises the original Open navigation and Activity path and verifies the opened
navigation. The original exact decision identities, unanswered/unknown input
preservation, badge-versus-approval distinction, private draft retention and
no-duplicate delivery assertions remain unchanged.

## Qualification and ownership

The full connection-inspection journey and both desktop and narrow-touch decisions
journeys passed in the owned Linux DTU with the unchanged packaged client. The
controlled old close/reload ordering failed at the same intercepted trigger.
These are synthetic-provider browser checks, not live-account or native-device
acceptance. Final source/state/process audit found no running references or
headless browser processes.

The existing source-census entries remain the owners:
`standard-ux-connection-inspection`, `standard-ux-decisions`, and
`standard-ux-decisions-touch`. Their direct entry points are
`tests/web/standard-ux-connection-inspection-browser.mjs` and
`tests/web/standard-ux-decisions-browser.mjs` (the latter uses
`STANDARD_UX_TOUCH=1` for touch). Both require an owned
`AMPLIFIER_TEST_ARTIFACTS` directory and the normal Python/Playwright prerequisites.
No frozen inventory or skip baseline changed. Exact source hashes, logs,
screenshots and the held-command trace are preserved under
`output/afk-approved-20261017/standard-transitions832/`.
