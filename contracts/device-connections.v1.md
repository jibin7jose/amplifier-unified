# Optional device connections — v1 PoC

Status: approved for isolated proof-of-concept implementation by the user on
2026-10-20. This is an experimental public extension of today's host, not an
OAuth implementation, AHP replacement or production adoption decision.
Parent: [client vision](../docs/clients/VISION.md).

## DV1 — Independent clients and optional capability

The host owns enrollment, grants, availability and delivery receipts. Clients
own local integration and presentation. No client imports host modules.
Ordinary chats and managed-browser operation do not require device enrollment.
Tempo and a new amplifier-app-cli experience are optional.

`GET /api/connections/v1` publishes protocol version, relative endpoint paths
and supported capability descriptions. It exposes no registered devices,
credentials, host paths, conversation content or account configuration.

For this host's supported browser-capable v1 discovery response, normal browser
navigation to the same origin's `/connect` is always available. Discovery and
the setup page are registered together. Anonymous navigation may redirect to
login, preserving `/connect` as the continuation. There is no server-supplied
setup URL in discovery: native clients open the fixed same-origin path after
validating the supported discovery shape, without passing a chat credential.
Discovery does not establish browser sign-in, extension enrollment, execution
permission, or target readiness. Unsupported hosts must leave chat access
unchanged rather than infer browser-control support.

## DV2 — Enrollment and proof

Clients POST `/api/connections/v1/enroll` with `name`, `appId`, `challenge`,
`grants` and optional `setupId`. Names/app IDs are unverified labels, not signed
application identity. The challenge is base64url SHA-256 of a random 32-byte
verifier. The response supplies `id`, `code`, `verificationPath`, `qrPath`,
`expiresIn` and `interval`, using the existing bounded pairing manager.
GET `/api/connections/v1/qr?id=...` renders that pending request's approval URL
as an SVG QR code; neither the URL nor the image contains a verifier or credential.

The owner signs in through the external browser and approves the exact grants.
The code can be entered at `/connect/code`; a complete approval link or QR
avoids typing. Approval requires an owner browser session and session-bound
CSRF proof. Native clients never receive the browser's session.

POST `/api/connections/v1/token` with `id` and `verifier` redeems only that
approved request. Retrying a lost response returns the same credential until
request expiry. Pending requests expire after ten minutes or host restart.
Denial is final; restart/expiry requires an explicit new attempt.

`amd_` credentials have the distinct `device-v1` scope. They can only access
their own connection endpoints; they cannot read chats, settings, credentials,
files or owner control APIs. Legacy `amc_`/`amt_` semantics remain unchanged.
Only credential hashes persist. Revocation removes access to all versions.

The bundled browser creates harmless setup context using POST
`/api/connections/v1/setups`. Setup IDs carry no authority. Native/extension
proof generation starts after installation, not when a download starts.
The owner can inspect `/setups/{id}` to observe approval/readiness. Request
context expires independently of the shorter authorization challenge.

## DV3 — Access and local availability

An enrollment requests explicit grants from the published catalog:

- `connection.ping`: bounded connectivity check, no local file or chat access.
- `browser.observe`: inspect the single browser document selected locally.
- `browser.act`: click/type in that selected document; requires observe.

Additional grants require a new owner-approved, proof-bound request. A client
can request an upgrade using its credential; approval updates that enrollment,
without widening other clients' grants or retyping the host. Denying or abandoning
an upgrade preserves the original grants. Application updates grant nothing.

POST `/self/heartbeat` supplies an instance ID and at most ten named targets.
Each target has `id`, `revision`, `label`, capability identifiers and readiness.
Host grants bound advertisement and admission. Client labels are escaped.
Readiness expires after 45 seconds without contact. Presence is ephemeral:
after host restart, enrolled clients remain paired and reconnect to advertise.
A closed/stopped browser target is not ready, even while its client is online.

Owner GET `/devices` lists paired/connected/ready state with bounded target
details. POST `/devices/{id}/revoke` removes access. These paths are under
`/api/connections/v1`; the existing `/devices` management page remains supported.
Cookie-authenticated mutations require CSRF; scoped credentials cannot fall
through to a simultaneously present owner cookie.

## DV4 — Bounded delivery and honest outcomes

Owner POST `/operations` names a unique `id`, `deviceId`, `targetId`,
`revision`, `action` and `args`. The service verifies online readiness, exact
target revision, advertised capability and grant. Domain adapters own action
schemas; no remote shell or arbitrary JavaScript endpoint exists.

For effectful actions, the host persists admission before returning `queued`. Repeating an operation
ID with identical input returns its receipt; different input conflicts. A
client POSTs `/self/poll`, receiving at most one delivery with an opaque lease
and deadline. The host persists `delivered` before sending it.

Delivered operations are never automatically redelivered. Deadline expiry,
host restart or a changed client instance marks unresolved delivery `unknown`.
An unexpired queued action becomes unavailable if its target changes. The
client checks the document identity and local stop state immediately before
execution. POST `/self/result` reports the matching ID/lease and a bounded
result. A late matching receipt can resolve an unknown outcome.

The receipt states are `queued`, `delivered`, `completed`, `failed`,
`cancelled` and `unknown`. Acceptance is not completion. Losing a receipt never
authorizes retrying the effect under a fresh ID. Effectful receipts are retained for at
least 24 hours; the bounded ledger rejects new work when it cannot retain it.

The domain action registry separately identifies side-effect-free observations:
`connection.ping`, `browser.observe`, `browser.task.observe`, and
`browser.task.screenshot`. Their admission and delivery carry
`retention: "transient"`. They use memory only, expire after 60 seconds, and may
be evicted earlier under capacity pressure after delivery settles. They do not
consume the durable effect ledger or retain screenshots on disk. At most 64
transient receipts (16 per device) are retained, with the same 256 KiB result
limit. At most two observations per device may be queued/delivered, within the
existing ten-operation total; effectful delivery has priority over observations.
No pending delivery is evicted for capacity. A missing or expired receipt
returns 404; a caller may request a new observation, never infer an input effect.

Reconnect callbacks serialize result recovery and polling as one operation.
An acknowledgement removes only its matching connection, operation and lease
receipt; it cannot erase a newer receipt or start another execution in parallel.

Clients preserve the delivered `retention` beside any locally pending result.
The Chromium extension stores transient results only in trusted-context
`chrome.storage.session`, never `storage.local`. Each result carries local
`createdAt` and `expiresAt` timestamps with at most 60 seconds of send eligibility.
Expiry is checked before initial delivery and every recovery attempt. A local
expiry alarm removes the cached result independently of service connectivity;
Stop, missing/revoked enrollment or an enrollment change also clears it. An
earlier extension version's transient result in durable local storage is removed
at the next worker wake without sending it. Durable and unmarked legacy results
remain in local storage and are not discarded by these cleanup rules, including
when their enrollment no longer matches the active connection.

Session storage survives background-worker suspension, but is cleared when the
browser restarts or the extension reloads/updates. Offline networking does not
extend send eligibility. Browser/OS sleep or alarm throttling can delay removal
from memory until the worker next wakes; an expired result is then discarded
before network delivery. This is not a claim that sleeping browser memory is
physically erased at exactly 60 seconds. No observation is replayed as part of
receipt cleanup. See [Chrome session storage](https://developer.chrome.com/docs/extensions/reference/api/storage#property-session)
and [alarm timing](https://developer.chrome.com/docs/extensions/reference/api/alarms).

If posting a transient result returns 404 or 410 after host restart/expiry,
discard that result receipt and resume polling without executing it again.
Other failures keep the existing receipt recovery behavior. Missing retention
means durable; uncertain input/navigation receipts must never be discarded by
this rule. The remaining lease, exact-target, Stop and no-redelivery rules apply
to both classes; transient delivery is not persisted before sending.

Owner cancellation and local POST `/self/stop` cancel queued work and mark
already delivered effects unknown. Local Stop immediately disables execution
before any network acknowledgement. Revocation blocks future admission and
delivery. Completed effects are not undone. Existing accepted chat work keeps
its separate contract.

## DV5 — Credential lifecycle and transport

The client stores the credential in its platform's private store. The PoC
extension uses extension-private local storage with content-script access
disabled; the reference client uses a 0600 file. Neither is a hardware
attestation or a substitute for native Keychain in a shipping native app.

POST `/self/rotate` with a durable random rotation ID replaces the credential
idempotently. A bounded previous-credential grace period permits recovery from
a lost reply. The new credential is derived from host secret and rotation ID,
so only hashes and rotation metadata persist. The client persists the new
credential before clearing its pending rotation ID. Revocation removes both.

Trusted HTTPS is required, with loopback HTTP permitted for development.
Origins and server redirects are validated. Extension-origin access is allowed
only to discovery/enrollment and the client's own bearer-authenticated APIs.
It never includes cookie credentials or owner APIs. Normal websites retain
the existing same-origin policy. Transport errors cannot become false success.

No automatic discovery, tunneling, host migration, silent software installation
or automatic certificate-trust bypass is provided.

## DV6 — PoC acceptance

Qualify the independent Python reference client and Chromium extension against
the same service. Exercise browser sign-in, denial, code entry, lost responses,
scope rejection, grant upgrade, heartbeat expiry, reconnect, host restart,
credential rotation/revocation, exact-document actions, local Stop and unknown
outcomes. Inspect real rendered browser results and retained receipts.

Native signing, store distribution, macOS/Windows packaging, managed-device
policy, SSO and production security qualification remain separate acceptance.
The extension is a development artifact; it must be described that way.

## Optional full-tab execution (extension 0.2)

A new `browser.task` grant permits observation and bounded control across HTTP(S) navigation in one tab explicitly selected on the device. Existing document grants never activate this mode. Adding the grant requires proof-bound owner approval; selecting the tab is still local. Stop detaches debugger access. The extension exposes only fixed observe/open/act/screenshot operations, never arbitrary scripts or CDP commands. Observed actions carry a short-lived, exact-document revision; input is checked atomically against current DOM state. The tab grant survives navigation, but an observation does not. Unknown effects are not replayed.

Screenshot capture checks the selected attachment and document before capture
and again before returning pixels, including the current URL exclusion check.
Navigation or Stop during capture discards the result. A later observation must
start from the current permitted document.

The Chromium debugger permission is an installation permission (Chrome does not allow it to be optional). Upgrading this development extension requires accepting the browser's permission change. The debugger is attached only when full-tab access is separately granted and selected locally. Existing 0.1 clients continue to use exact-document operations. Protected browser pages and this service's administrative pages are excluded. Full-tab screenshots are bounded; result bodies are limited to 256 KiB and total request bodies to 512 KiB.

Reference: [Chrome permission constraints](https://developer.chrome.com/docs/extensions/reference/api/permissions#step_2_declare_optional_permissions_in_the_manifest).
