Amplifier Connect — optional browser access preview (0.2.2)

Unzip this folder. In Chrome or Edge, open Extensions, enable Developer mode,
choose Load unpacked and select this folder. From your Amplifier service,
open Devices & apps and choose Open and connect.

Updating an existing 0.2.1 installation: choose Stop browser access, replace
the files in its existing unpacked directory, then Reload that same extension in Chrome or Edge's
Extensions page. Verify version 0.2.2 and extension ID
hihclhdmelacndmdafanjafjbmaobgeo. Keep the supplied manifest key unchanged.
This patch fixes screenshot boundaries and pending-receipt recovery. It adds
no permissions or grants and preserves enrollment; do not remove/reinstall or
pair again for this update. Stop clears the selected tab; reload drops pending
observations. Select the intended tab again explicitly when ready to resume.

This is an unpacked development extension, not a browser-store release.
It connects directly to your Amplifier service; a native companion is optional.
Browser access is approved separately from chat access. The extension asks for
access to the service you choose, then you approve its requested capabilities.
Opening setup from a companion app does not enroll the extension or grant access.

Choose the browser access you need:
- Use this tab: controls the selected document. After navigation, select the new
  document again from the extension.
- Enable tasks across pages: request and approve this added access on the
  connection page, then open the extension on the intended tab and choose
  Use this tab across pages. Amplifier can observe and control that one tab
  across normal HTTP(S) page navigation until you stop it.

This preview requests the browser's debugger permission during installation or
update. It attaches only after across-pages access is approved and you select a
tab locally; the browser displays a debugging indicator while attached. Neither
mode grants native desktop control or access to other tabs or profiles. Browser
internal pages, OS dialogs, embedded frames and canvas-only controls are outside
this preview's supported web controls.

Stop browser access in the extension stops local execution and detaches debugging
without waiting for the service. Pairing remains available; select a tab again to
resume. Removing access in the service revokes enrollment. Quitting or
disconnecting a native companion does not stop this independently running
extension. Use Stop browser access or remove access in Devices & apps to end it.

The keyboard shortcut Ctrl+Shift+Y (Command+Shift+Y on Mac) opens the extension.
Only one service connection is supported by this preview installation.

Pending screenshots and other read-only observations stay only in browser-session
memory, with at most 60 seconds to send the result. An independent expiry alarm
clears them even when the service is offline. Stop or a disconnected/revoked
enrollment clears them; browser restart or extension reload/update drops them.
Sleep or browser throttling can delay memory cleanup until the next worker wake,
but expired observations are never sent then. Old preview observation results
stored on disk are removed at the next wake. Pending input/navigation receipts
remain durable for recovery; losing or expiring a result never repeats an action.

HTTPS must be trusted by the browser, including its background connections.
Continuing past a certificate warning can let approval work while background
requests fail. Use the service's HTTPS setup instructions to establish trust,
restart the browser, then retry. Setup verifies the background connection before
reporting Connected. Connection help lets you retry without pairing again.
