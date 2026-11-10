# Device connection PoC — isolated qualification, October 20, 2026

**Historical connection baseline.** The subsequent [normal-chat UI-use integration](ui-use-integration-20261020.md)
adds managed browsers, optional decision selection, canonical desktop execution,
in-app controls and extension 0.2.0 across-page tasks. Its guide and receipt are
the current preview instructions; the measurements and limitations below describe
the earlier connection-only qualification.

The generic connection service, bundled setup page, development browser
extension and independent reference client are implemented and qualified in an
owned Linux ARM64 DTU. The preview was retained for review. Nothing had been
published, deployed to a shared instance or installed on the user's Mac.

This is the connection and execution foundation. Chat-driven selection of this
device route through UI-use is a subsequent integration; this PoC's sample
uses the public connection protocol directly and calls no model.

## Follow-up: certificate recovery and extension 0.1.1

The user reported **Failed to fetch** in the Mac extension popup after choosing
**Use this tab**, having continued past the browser's certificate warning. The
service had seen approval and a brief connection, followed by offline status.
The user confirmed that trusting the preview CA and restarting the browser fixed
the problem and retained their pairing.

An isolated Chromium fixture reproduced the mechanism: page and extension-page
requests worked after the warning exception, but a new TLS connection from the
background worker failed with `ERR_CERT_AUTHORITY_INVALID`. Reused connections
can temporarily hide the difference. The negative fixture deliberately closes
connections; the successful recovery uses the owned DTU's trusted certificate.

Extension 0.1.1 now checks background connectivity before reporting Connected,
shows connection help for transport failures, and offers Retry without a new
enrollment. The popup distinguishes saved access from current connectivity.
It also passes its own active tab identity explicitly to the background worker;
the worker no longer infers a current window it does not own. First-install
instructions link HTTPS setup before pairing.

Four certificate/recovery checks passed in
`tests/web/device-connection-tls-browser.mjs`, followed by all fourteen
normal browser/client checks. The separate installed-wheel check verified the
new network helper and extension version. Evidence is retained as
`extension-tls-recovery.json`, `extension-011-browser.json`,
`extension-011-browser-third.log` and `package-check-011.json` in the task's
evidence directory. Existing Python authorization code was unchanged; the
earlier 79-test result remains historical evidence, not a newly repeated run.

The browser harness now selects its exact test enrollment, so a real connected
Mac cannot be mistaken for the test's executor. Test cleanup revokes only test
enrollments. No Mac control action was sent during this investigation. The
user-confirmed recovery is useful Mac evidence, not full platform qualification.

The new extension is downloadable from the retained preview. An already loaded
development extension updates by replacing files in its existing folder and
choosing Reload on the browser's Extensions page; its stable identity preserves
pairing. Certificate trust resolves the original failure with version 0.1.0 too.

## Reproduce the connection flow

Open `/connect` on your reachable Unified service from the computer whose browser
you want to make available. Use that service's sign-in and trusted certificate.
For a private certificate, follow `/setup` and verify the CA fingerprint with
the deployment owner before trusting it. The extension requires trusted HTTPS;
a browser warning exception is insufficient. Private deployment addresses,
account details and certificate records are retained separately from this guide.

1. Sign in and choose **Download extension preview**. Unzip it. In Chrome or
   Edge's Extensions page, enable Developer mode, choose **Load unpacked** and
   select the extracted `amplifier-connect` directory. This is a development
   extension, not a store release.
2. Return to **Devices & apps** and choose **Open and connect**. Accept the
   browser's service-access prompt. Compare the displayed connection code and
   choose **Allow connection** in the service approval page. The host address
   is carried through automatically; no token copying is needed.
3. Open the linked **sample form** on that computer. Open the extension from
   the browser toolbar and choose **Use this tab**. Keep that document open.
4. Open the same Devices & apps URL on another device, or another browser,
   and sign in. Select the ready browser target and choose **Fill and save the
   sample**. Watch the original browser change and confirm **Saved Ada blue.**
   Expand **Requests and results** for the actual operation inputs and receipts.

The extension's **Stop browser access** immediately disables its selected
target. **Remove access** on the service revokes the whole app enrollment.
Selecting a new document after navigation requires a fresh local **Use this
tab** gesture. A routine browser restart retains enrollment, so it does not
require approval again.

The page also offers a Python reference client. It uses only the public
protocol, opens approval in the external browser and provides a connection
check. It has no browser or desktop execution capability. Its `--ca` option
allows explicit use of this preview's verified CA.

## Source and ownership

| Item | Qualified value |
| --- | --- |
| Architecture | Linux ARM64 in a task-owned DTU |
| Branch | `codex/device-connect-poc-20261020` |
| Baseline main | `79f7fb306c780eecd4e1c2c5c7f5fcb2f6a7f19d` |
| App version / built frontend ID | `0.20.187` / `c9832e50ecea63c0` |
| Source and state | Private editable checkout, dependencies, state and history |
| Serving processes | Owned backend and frontend build watcher |
| Transport | Dedicated HTTPS listener |
| Browser under test | Chromium, installed inside this DTU |
| Runtime | Python 3.13.16; Node 22.23.3 |

The backend uses an editable install; the bundled frontend has an owned build
watcher. Source, dependencies, synthetic account, state, history, caches, CA
and browser contexts are private to this DTU. Existing decision-model previews and shared/live installations were
preserved.

The final source commit, transferred-file hashes, source parity result and
cleanup record are retained in the private operational evidence. The initial
upstream inspection saw `1952fef7f`; the branch was created from its descendant
`79f7fb3` after the shared remote-tracking reference advanced. Measurements
refer to the latter baseline and this branch's recorded changes.

## Implemented boundary

The [experimental contract](../../contracts/device-connections.v1.md) defines
public discovery, proof-bound enrollment, explicit grants, credential rotation,
presence, named targets, bounded delivery, receipts, local stop and revocation.
The implementation reuses the existing pairing manager and credential store.
It adds a distinct `device-v1` scope; existing companion access does not gain
browser authority.

The service owns admission and delivery. Browser action schemas are separated
from the connection transport. The extension owns local permissions, document
selection and execution. An independent Python process consumes the same
protocol without importing any Amplifier modules.

The extension uses an outbound connection and Chromium's `activeTab` permission.
It acts on the exact selected top-level document. It does not expose arbitrary
JavaScript, a remote shell or a general debugger API. A changed document cannot
silently inherit the old selection. A delivered operation is never
automatically redelivered after uncertainty; the service retains an `unknown`
outcome until a matching late receipt resolves it.

No new repo was needed for the PoC. The public contract, host implementation,
bundled setup UX and downloadable reference implementations are colocated in
Unified. Independently released clients can implement the public protocol.
Tempo, a new CLI experience and a separate broker are not required.

## Qualification

The following evidence artifacts are retained in the private qualification archive.

| Check | Result / retained evidence |
| --- | --- |
| Baseline affected Python tests | 64 passed; `baseline-corrected.log` |
| Final affected Python tests | 79 passed in 32.80 s; `qualification-with-stop-recovery.log` |
| Existing companion browser pairing | Passed before and after; same logs |
| New rendered browser/client flows | 14 checks passed; `device-connections-browser.json` |
| Actual service restart and lost-response recovery | 5 checks passed; `live-recovery.json` |
| Separately installed built wheel | Passed; `package-check.json` |
| LAN health with CA verification | HTTP 200; `lan-readiness.json` |
| Mobile-width rendered result | `connection-completed.png` |

The focused Python run covered connection authorization, grants, proof,
expiry/denial, retries, target admission, stop/revocation, persistence failure
and recovery, together with existing authentication, pairing, terminal setup
and companion-authority behavior. It was not the full repository suite.

The browser qualification used actual PAM sign-in and trusted HTTPS. The
private CA was installed into the owned test browser's trust store; there was
no certificate-validation bypass. An actual browser service-access prompt
was accepted through the isolated display. A real toolbar gesture granted
`activeTab`. A separate-origin page was then controlled without a persistent
site grant.

An independent second browser process, using a 390-pixel mobile viewport and
its own sign-in, selected the desktop browser target. Five operations observed
the form, filled Ada, selected Blue, saved, and verified the result. The page's
save counter was exactly one. **This measured run took 4.382 seconds and made
zero model calls.** It measures connection/executor behavior, not model
quality or a decision-model performance comparison.

For an already installed extension, measured enrollment needed **two product
decisions and one browser permission prompt**, with no typed host address or
copied token. Installation, certificate trust, initial sign-in and choosing the
working tab are additional steps and are excluded from that count.

Other real-browser checks covered local stop, navigation invalidation, credential
rotation, browser restart without new approval, independent reference-client
delivery, and revocation observed by both clients. QR rendering and comparison
code approval passed; no physical phone camera scan was performed.

The service was actually restarted during fault qualification. Delivered work
became `unknown`, queued work was cancelled, and reconnect did not replay either
effect. A matching late receipt resolved uncertainty. A lost credential-rotation
response was recovered after restart, and revocation rejected the rotated
credential. The measured restart-to-health interval was 0.55 seconds; this is
one local run, not a latency guarantee.

The wheel was installed into a separate environment. Checks imported the
installed package and exercised discovery, rendered setup, packaged resources,
extension download, independent-client download, QR SVG and bundled frontend
identity. They did not rerun every browser test against the wheel.

## Limits and next integration

- Linux Chromium was qualified. Edge, Safari, Firefox, a physical phone, macOS
  browser execution, enterprise policy, managed-device SSO and Tempo were not.
  Chrome/Edge are the intended extension targets; cross-platform qualification
  remains necessary.
- The extension is installed manually in Developer mode and supports one
  service connection. Store distribution, signing and native installers remain
  follow-up work.
- Exact-document selection and a small observe/click/type action set establish
  the execution boundary. General multi-page automation, iframe execution and
  debugger access are not implemented.
- Enrollment persists until revoked. Rotation has a bounded previous-credential
  recovery window. This PoC is not an OAuth authorization server or a full
  production credential-lifecycle/security qualification.
- The capability catalog is explicit and bounded. Arbitrary third-party
  capability plugins are not dynamically loaded by the host.
- No model was configured for this preview. Connecting this route to shared
  UI-use selection, decision models, managed-browser fallback and chat-level
  preferences is subsequent work. The existing decision-model preview remains
  available separately.

The next integration should let UI-use discover ready authorized targets through
this public boundary, preserve exact target identity through each action, and
surface unavailable/stopped/unknown outcomes. Ordinary and managed-browser
routes must remain usable without this extension or Tempo. Before making this
an end-user default, qualify installation and recovery on the actual target
platforms and exercise it through a real agent task.

## Retention and cleanup

The owned DTU, editable checkout, services, synthetic test account, needed
dependencies and evidence were retained for review. Temporary source archives,
the separate wheel-check environment and inactive test fixtures are removed
after evidence is preserved. Browser acceptance profiles are disposed by the
test harness, and its created enrollments are revoked.

Retirement is explicit: verify ownership, active review and Git state, preserve
recoverable source commits, then retire only the named task-owned resources.
Exact environment identities and cleanup commands are recorded in the private
workspace manifest rather than in this public qualification summary.
