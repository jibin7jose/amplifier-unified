# Connect clients and devices with minimal user effort

Status: **original discussion proposal, 2026-10-20**. The user subsequently
authorized an isolated PoC. Its implemented scope is defined by the
[experimental connection contract](../../contracts/device-connections.v1.md);
the [qualification and trial guide](../validation/device-connection-poc-20261020.md)
records what now works and what remains unimplemented. This proposal's source
pins and descriptions below preserve the evidence available during discussion.
No production adoption or change to Tempo's existing grants is implied.

## Direction

Unified's service should enable independent clients to build complete experiences.
It owns authentication, permission enforcement, durable host identity, work and
capability availability. Clients own presentation, onboarding and local platform
integration. The bundled web UI is a useful first experience and installation
starting point, not the only client permitted to construct an experience.

This follows the existing family vision and HP4–6, CS1/4/8 and CB1–3 contracts.
It does not require the AHP/ACP replacement before improving today's service.
Use public, versioned boundaries in the current host; preserve the intended
protocol ownership and avoid inventing a second conversation protocol.

The user's goal is few interactions: reuse known host/account context, avoid
copied secrets and repeated setup, and confirm readiness automatically. Treat
clicks, typed fields, context switches and recovery steps as costs. Preserve
meaningful permission choices and required platform confirmations.

Reachability is user/operator supplied for this phase: LAN, Tailscale or another
reachable deployment. Do not add a mandatory relay, network manager or hosted
identity service. Trusted transport still matters; reachable does not mean
authenticated or permission to bypass certificate validation.

## Existing foundation

Reviewed Unified main `3a9f4bbf791651be018fd55ae1dedb275211a4c9` and native Tempo
`c2d59f4ca97e13a01747b3ddf0908a4c7ae8881f`.

The current [native approval contract](native-pairing.md) already supplies:

- Client-created proof verifier and a challenge-bound pending request.
- A browser approval link, comparison code, bounded polling and expiry.
- Approval by the signed-in host owner, without copying a control token.
- Revocable credentials in the existing device store, with hashes on the host
  and Keychain storage in Tempo.
- A list/revoke page and client-ID binding to the approved registration.

It is deliberately limited to `companion-v1`: chat observation and bounded
create/send operations. Current authentication hardcodes that scope/token family;
it is not arbitrary capability enrollment, browser authority, OAuth device-grant
conformance, hardware identity or device-compliance attestation.

Tempo's inspected settings require an address and open the host approval page.
Its Info.plist does not register a custom connection URL scheme, and its reviewed
application has no web-initiated connection handler. General web-to-app
onboarding, credential rotation/renewal and a generic device execution channel
are new work, not existing features implied by the pairing page.

## Generic service responsibilities

| Service capability | What independent clients can build with it |
| --- | --- |
| Discovery | Learn the host identity, supported protocol versions, authentication/enrollment methods and permitted service capabilities. |
| Enrollment and grants | Request narrowly described access, complete owner approval, store a client-specific credential, inspect or revoke grants. |
| Capability registration | Offer authorized local features and named targets, reporting actual readiness and limitations. |
| Connections and recovery | Maintain outbound authenticated connections, subscriptions, resumable status and appropriate credential lifecycle. |
| Optional device execution | Deliver a bounded action to a named authorized target and expose receipts, cancellation and unknown outcomes. |

Discovery before authentication exposes only appropriate public metadata.
Authorized discovery exposes only the resources and targets the principal may
see. Registering a capability does not authorize its use, and an advertised
capability does not prove readiness.

Two relationships use the same enrollment foundation:

1. A client **consumes** host services: chat, activity, notifications or other
   granted operations.
2. A companion/executor **offers** local capabilities: an enrolled browser,
   desktop observations, file access or another explicit service.

These are distinct grants. Connecting a chat companion cannot silently enable
the host to control its device. Host-side task authority and device-side local
permission both apply. A connected full native client can support both roles;
a TUI or web client need not.

Keep app installation identity, authenticated principal, runtime client
connection, physical-device label and execution target distinct. Multiple apps
on a Mac do not share a credential merely because they report the same hostname.
Friendly grouping in “Devices & apps” must not merge their authority.

The service enforces access and admission. It does not choose the client's
layout, require Tempo, prescribe which model a browser task uses, or implement
each platform's click/keyboard code. Those remain client, agent and capability
provider responsibilities.

## One connection service, several entry paths

### Existing application on the current device

1. In the web UI, choose **Connect this computer** or a contextual action such
   as **Use my browser**.
2. Open the selected compatible application with the intended host already
   supplied. The native client generates its own proof and begins enrollment.
3. Review one clear approval for the actual host, requesting application and
   requested access. Reuse the existing browser sign-in when possible.
4. Both clients observe confirmed connection/readiness; return to the original
   task without another “I finished setup” message.

Target: two product decisions—start connection and approve the described
access—plus any required browser/OS app-opening confirmation. This is a design
target, not a measured universal click count.

A launch link carries host/setup context, never a durable access token. It is
not itself authority to connect. Bind final approval to the native client's
proof and actual request. Validate origins and return routes; do not trust a
friendly app name or a URL scheme as proof of application identity.

Support arbitrary reachable host origins without assuming every self-hosted
domain can participate in an app publisher's universal-link arrangement.
Choose the platform handoff and callback method during implementation; keep
proof-bound authorization independent of that choice.

### Application not installed

The web client offers the appropriate signed application or extension-store
listing, explains the requested capability, and preserves the originating task
and host context. After installation, **Open and connect** continues setup.

Start the short-lived authorization challenge when the application is ready,
so download/install time does not consume its approval window. Preserve harmless
setup context separately. The browser cannot silently install native software,
grant OS permissions or reliably detect every installed app; use positive
connection acknowledgements rather than claiming readiness after a download.

Package/store installation and OS consent remain visible, but users should not
enter endpoints, edit configuration, register native helpers, copy secrets or
repeat installation steps to pair. Platform-specific installers own helper
registration and updates.

### Another device, TUI or headless client

Offer an openable verification link and QR code, with a short-code alternative.
The user approves on a signed-in browser. Only the initiating client's proof or
device credential request can complete the exchange. Its connection status
updates automatically.

For first-time enrollment from a different device, the host can supply a setup
link/QR carrying its address; alternatively the client accepts the host address
once. There is no assumption of automatic LAN discovery.

## Standards and terminology

The desired experience is compatible with established authorization patterns:

- Native applications with a browser use external-browser authorization and
  proof-key protection when implementing OAuth. Reusing the browser's sign-in
  avoids asking the user to log in inside another app.
  [RFC 8252](https://www.rfc-editor.org/rfc/rfc8252.html)
- Input-constrained and cross-device flows can use device authorization. QR or
  complete verification links remove typing while retaining a meaningful
  comparison and approval.
  [RFC 8628](https://www.rfc-editor.org/rfc/rfc8628.html)

Device-code entry should not be mandatory for a capable same-device client.
RFC 8628 itself distinguishes this from native-browser authorization.

The current challenge/verifier pairing API resembles these patterns but is not
the OAuth wire protocol. Reuse its store, policy and proof-bound enrollment
foundation. Evaluate established authorization implementations before adding
standard OAuth endpoints; claim conformance only after protocol qualification.
Do not bolt on a homemade “OAuth-compatible” label or make a new identity provider
a prerequisite for the UI-use milestone.

## Incremental capability setup

A generic connection should not request every possible local permission.
“Follow my work” can connect a companion without browser or desktop authority.
Later, “Use this browser” adds its profile/site access through the same connection
management experience, without repeating host login and base pairing.

An application update does not expand an existing grant. New capabilities need
the appropriate incremental approval. Revocation remains per application/grant,
with local Stop available to execution providers.

The lifecycle should distinguish paired, connected and ready. A device can be
paired while offline, or connected while its browser extension is disabled.
Reboot and ordinary reconnect should reuse valid enrollment; revoked access
cannot be restored by background retries. Renewal/rotation must have explicit
failure, expiry and revocation semantics, not require manual token replacement.

Device actions retain operation identity and bounded receipts. Reconnect
refreshes capabilities and state; it does not repeat clicks or submits.
Chat work accepted before companion revocation can continue under its existing
contract. Device-control revocation stops further local action admission and
invalidates execution leases; completed effects are not undone.

## Web UI and distribution responsibilities

The bundled web UI can provide **Devices & apps**, install links, progress,
approval, capability explanations and **Disconnect**. Another client may offer
a different layout using the same authorized service operations.

Recommended client/install metadata belongs in optional distribution/catalog
data with trusted publishers, supported platforms and compatibility information.
The service core does not hardcode “browser means Tempo,” mandate a single
native application, or run arbitrary installer commands supplied by a descriptor.

When setup was requested by an existing task, expose structured progress:
waiting for installation, waiting for approval, connected, capability ready,
unavailable or cancelled. The authorized task may continue from fresh state
when its prerequisites become ready. Pairing alone must never start unrelated
work or resurrect a cancelled task.

If a route is unavailable, clients can offer managed browser use, another
authorized executor or assisted screen sharing. Ordinary conversation and
optional decision-model defaults remain independent of device enrollment.
Fallback must preserve the task's account, target and permission constraints.
Screen sharing provides observation and guidance, not automatic input control;
an uncertain prior action must be reconciled before trying another executor.

## Ownership correction and delivery slices

This refines the earlier UI-use proposal: generic enrollment, grants, client/
device identity, availability and connection lifecycle belong with Unified's
service contract and existing device store. They do not belong inside the
UI-use bundle or a Tempo-specific pairing service.

UI-use owns browser/computer task semantics and consumes registered capability
adapters. Browser execution schemas, extension and browser-specific readiness
remain domain components. Tempo owns its Mac application and local helper.
Other clients implement the public connection contract independently.

Start in the existing `amplifier-unified` repository, with a proposed public
contract and test peer; use existing client repositories for presentation and
platform work. A small shared schema/client package can follow demonstrated
reuse. No new always-on broker or mandatory repository extraction is needed.

Suggested slices:

1. Generalize the connection contract and map the existing companion grant
   without widening it. Specify identity, consent, lifecycle and absent-capability
   behavior. Review and ratify new promises before implementing them.
2. Qualify one web-to-existing-client setup flow with automatic host handoff,
   explicit approval and acknowledged readiness. Tempo can be an optional first
   consumer, with an independent fixture client proving the service boundary.
3. Add install-and-continue and cross-device QR/code entry using the same
   enrollment foundation. Measure actual steps and interrupted setup recovery.
4. Add optional execution capability registration and grants for browser use.
   This does not require shipping a new CLI experience; generic client examples
   can demonstrate protocol portability.

The general connection work must not become a prerequisite for ordinary managed
browser use. Base UI-use can ship with unavailable-device handling while these
optional connection and executor capabilities are developed separately.

Keep automatic network reachability, NAT traversal, remote installation, host
migration and a full device-management system outside this phase.

## Acceptance

Measure product clicks, platform prompts, typed fields, context switches, elapsed
time and recovery steps separately. A low click count is not sufficient if users
cannot tell which host/app they connected or what access they granted.

Verify install-present, fresh install, another-device and constrained-client
paths; existing browser sign-in reuse; multi-host selection; expired/denied
requests; lost replies; host restart; client reinstall; revocation; changed
capabilities; and a client other than Tempo. Native packaging and real-browser
handoff require actual-device acceptance.

Required outcomes: no copied bearer token, no endpoint re-entry when starting
from the host web UI, no false success from download completion, no repeated
pairing on ordinary reconnect, no scope expansion from an update, and no effect
replay when a connection recovers.

This is source/standards research and a design proposal. No application instance,
credential, device installation, account grant or runtime was changed.
