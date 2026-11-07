# Amplifier web frontend

React SPA served by the Python host. The compiled application is committed in `../amplifier_web/static/`; installing or launching the Python package does not require Node.

```
npm install
npm run build
npm test
```

For frontend development, run the Python server on port 8765, then `npm run dev`.
Use an owned isolated environment under the workspace's testing rules; the
[Vite configuration](vite.config.js) proxies API requests and WebSockets to that server.

## Shared interaction surface

App actions use `POST /api/actions`; `window.amplifier.getActions()` and
`window.amplifier.dispatch(name, args)` expose the same action surface. The
[live-session contract](../docs/clients/live-sessions.md) owns attachment,
explicit conversation targets, receipts, recovery and device effects. The
bundled client and server evolve together; independently released clients retain
their public contracts.

The [state stream adapter](src/state-stream.js) uses WebSocket events when the
attached host advertises support, with SSE for hosts that do not. Each stream
starts with a `state` snapshot and applies `state-delta` updates to its own
revision baseline. HTTP receipts and recovery reads do not replace that baseline.
Reconnect starts a fresh stream without replaying commands. See
[browser state transport](../docs/browser-state-transport.md) for bounded
projections, shell events and recovery behavior; measure construction work as
well as bytes under the [performance guidance](../docs/validation/performance.md).

`window.amplifier.getState()` exposes this browser's state, including private
input, plus a redacted rendered view. It is not the model-facing state projection.
The [browser observation code](src/api.js) excludes private inputs from automatic
`/api/view` reports and rendered-view inspection. Ordinary agent readback also
excludes unsent composer text, message edits and attachment drafts. User-requested
inspection uses the exact `/view/draft` or `/view/messageEdit` pointer and an
explicit `clientId` displaying the calling conversation; a broader read never
opts in. [The projection owner](../amplifier_web/agent_private_state.py) and
[CS2](../contracts/client-state.v1.md) define this boundary.

Privacy of model readback does not imply client-only storage. The current host
still stores acknowledged client drafts; the [draft journal](src/draft-journal.js)
keeps unacknowledged composer edits in browser session storage for recovery.
The [client vision](../docs/clients/VISION.md) describes the target local-only
persistence model, not a completed migration. Settings editors have their own
[draft and credential boundaries](../docs/SETTINGS.md#boundaries-and-state).

Browser effects (call media, notification permission, and file download) are delivered in `deviceCommands`, with command IDs deduplicated against HTTP responses. Existing effects are not replayed when opening the app. Microphone and notification grants remain browser permission prompts.

## Themes

`src/unified.css` is the complete Amplifier Unified skin. It includes an embedded Amplifier logo from microsoft/amplifier and has no external asset dependencies. The build always links a real CSS file in HTML; theme application does not depend on enabling a disabled stylesheet or on `@scope` support. The same source is copied to `static/unified.amplifier.css` for reset and export.

The stable root is `#amp-one`, with named `data-part` surfaces. A skin can replace
variables or component styles. See [themes](../docs/shell/themes.md) for surface
names, library/editor operations, preview versus Apply, and portable CSS validation.
Applied skins are validated by the Python service; use embedded data assets rather
than remote URLs.

## Validation

`npm test` runs the frontend unit tests; production bundling validates JSX/imports
and generates the committed static application. Maintained browser journeys are
listed in [package.json](package.json), including state transport, draft recovery,
Settings and appearance workflows. Select affected checks and run
application instances in the owned DTU with synthetic state. Record exact source,
results and limitations; a fixture pass does not prove live-provider or device
acceptance. Follow the [release qualification policy](../docs/validation/release-qualification.md)
for focused local checks, generated-asset parity and separate broad background
coverage.

The [build identity owner](scripts/build-identity.mjs) includes authored browser,
public, branding and imported sibling inputs so a same-version rebuild can offer
reload. Add new cross-root build dependencies there. Generated copies are excluded;
the locked dependencies and normal build environment are assumed. This source ID
does not replace release qualification's full-tree, toolchain and exact-asset proof.
