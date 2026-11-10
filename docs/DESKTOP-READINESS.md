# Desktop and browser setup

Open **Settings → Desktop & browser → Check desktop and browser setup**, or call
`app_control(operation="dispatch", action="desktop.readiness", args={})`. The optional `args.sessionId` selects
the conversation; agent calls are bound to their calling conversation. UI and
agent use the same action and receive the same report.

Checking runs the existing bounded native `status` helper and reads metadata
from an already-ready worker. It never starts or revives a worker, mounts a
tool, selects a provider, opens a browser picker, requests OS permission,
captures content, activates a target, or clears a halt. Native status is an
availability preflight, not a successful physical observation.

## Separate owners and setup paths

| Capability | Owner | Evidence and next step |
| --- | --- | --- |
| Native foreground screen observation | Serving app host, using its exact `sys.executable` with an isolated helper | Status distinguishes missing library, unsupported OS, required/unknown Screen Recording permission, and ready for explicit consent. **Install native screen observation** explicitly adds the optional `native-desktop` extra through the guarded app updater. A worker install does not suffice. Ordinary updates preserve installed extras and do not opt into an absent extra. |
| Browser-selected screen observation | Browser displaying the conversation (text or voice) | Only an explicit source grant supplies its browser-reported kind and label. Use **Choose screen source** on localhost/HTTPS in a browser supporting display capture and fresh video frames. The browser picker needs a user click. No tab URL, browser profile, account, or accessibility text is exposed. |
| Optional mutable desktop/browser tools | Conversation worker or selected tool transport | The report shows the worker interpreter, configured tool modules including disabled entries, actual mounted tool names, and whether desktop doctor is advertised. Missing/retired workers remain unavailable rather than being started by inspection. Package installation alone is not a mounted tool or successful operation. |

The local host label uses Foundation's owner hostname and includes a distinct
app instance ID. It is not a hardware identity. The interpreter path does not
identify the parent application in macOS privacy settings. A native host can
differ from the browser device; selecting a browser source does not retarget
computer tools.

## Existing shared actions remain authoritative

- **Install native screen observation** appears after a setup check on a packaged
  Microsoft app when the extra is absent. It dispatches `updates.featureInstall`
  with `feature="native-desktop"` and the checked `host.instanceId` as
  `hostInstanceId`. Agents use the same action after an explicit install request.
  It qualifies the same serving app revision and every existing component before
  the normal idle/queue guards permit replacement and restart. Read the returned
  `requestId` in `updates.featureResults`; accepted, qualified and restart pending
  are not installed. An uncertain result must be inspected before retrying.
  Development checkouts and unverified/custom installations require their
  deployment owner. See [optional app feature installation](UPDATES.md#optional-app-features).
- **Bundles & modules** opens the existing bundle review/configuration UI.
  Use `bundle.discover`, review the offered composition, then explicitly add or
  enable it through `bundles.add` / `bundles.toggle`. Inspect an existing
  conversation's resolved modules before changing it. No configuration is
  inferred from a skill name.
- **Smart Tools connections** opens the existing connection, account identity,
  progressive schema, and installation workflow. Inspect the selected adapter's
  actual catalog. A connected adapter may expose browser targets or account
  identity through its own operations; this setup report never invents those
  fields or assumes it shares the user's normal browser profile.
- **Open computer target setup** opens **Session tools**, prefilled with
  `computer_use_unavailable` and `{"action":"discover"}`. Refresh the catalog
  and explicitly run discovery when desired. The module owns discovery,
  `activate`, and separate `persist` operations. Checking readiness runs none
  of them. See the consumed [computer-use library's setup contract](https://github.com/microsoft/amplifier-bundle-computer-use).
- **Open desktop doctor** opens the same tool UI with `desktop` and
  `{"action":"doctor"}`. Refresh the catalog, then run it. Agents use the same
  `runtime.control` action, operation `tool.invoke`, with `name="desktop"` and
  those arguments. Existing idle checks and policy hooks still apply; a policy
  may request approval even though doctor itself never prompts TCC or captures
  content. Mutable actions retain their module's presence/write guards.

Doctor's `bound_target` is the mounted computer target. `target_mode` and
`safety_state` describe tool policy and last recorded guard state, not a browser
tab selection. Remote permission/display fields remain connection-time
snapshots with their original ages. `action_surface.works` is structural support
without a known block, not proof of permission or physical success; consider
`effective_policy`, blocked actions and unknown permission values too. Saved
tool results are historical observations, not continuously refreshed facts.

Readiness results are returned directly, not saved as global current state.
The UI clears its snapshot when its conversation, active voice call, source
grant or confirmed installation changes, and discards delayed responses from the old context. Check again
after changing tools or OS permissions. Shared source grants still expire and
are revalidated at capture time by the [screen observation contract](VOICE-VISUAL.md).

## Validation boundaries

`tests/test_desktop_readiness.py` checks passive worker transport, caller scope,
separate host/worker identities, source lifecycle and bounded status data.
`tests/web/desktop-readiness-browser.mjs` runs production UI/routes in a
real isolated browser with synthetic native/runtime facts: blocked and ready
states, existing tool invocation, aged diagnostic facts, setup navigation,
UI/agent parity, source/conversation changes, failure handling and mobile layout.
The feature flow uses real updater admission and receipts with synthetic package
metadata, installer processes and restart requests. It covers explicit clicks,
duplicate prevention, failed qualification, pending restart, exact successor
confirmation, and separate OS permission after installation.
Run it with `AMPLIFIER_TEST_PYTHON` pointing at a test environment; optional
`AMPLIFIER_ACCEPTANCE_DIR` saves screenshots.

These tests do not prove OS consent, physical microphone audio, native capture,
commercial account behavior, or installed-host adoption. Those require an
explicitly granted real-device acceptance: one connected call, the named host
grant, a harmless foreground-window capture, then revocation/source replacement
and call-end cleanup. Screen observation requires Screen Recording, not
Accessibility. No control or permission grants are restored after restart.
