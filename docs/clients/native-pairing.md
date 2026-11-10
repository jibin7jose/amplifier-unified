# Native device approval — v1

The host owns browser approval and revocable device credentials. Native clients
own private proof keys and OS credential storage. This implements the shared
client vision without requiring Tempo, changing existing client authentication,
or embedding a runtime in a native app.

1. The client creates a random 32-byte verifier, posts its base64url SHA-256
   challenge to `/api/devices/pair`, and retains the verifier in memory only.
2. The host returns a random request ID, a comparison code, a relative browser
   approval URL, a 5-second polling interval, and a 10-minute expiry. No host
   credential travels in a URL. The native app opens that URL at the exact host
   origin and displays the comparison code.
3. The signed-in owner compares the code and explicitly allows or denies the
   request. Approval uses a session-bound CSRF proof and trusted HTTPS (loopback
   HTTP is allowed for isolated development). Request names are untrusted labels.
4. The native app polls `/api/devices/pair/token` with its request ID and verifier.
   Only approved requests with the matching proof receive a device credential.
   A lost response can be retried until expiry without creating another device.
   Host restart expires unfinished pairing; the client starts again explicitly.
5. Credentials use the existing device store and revocation mechanism. Only
   hashes persist on the host; the client stores its credential in Keychain.
   `/devices` lists registrations and lets the signed-in owner revoke them.

The `companion-v1` scope permits native attachment, compact activity/catalog
reads, bounded message/work previews, managed-chat creation and sending chat
messages. It does not permit arbitrary actions, settings, files, terminal setup,
provider credentials, approvals, or device administration. It is not a
per-conversation ACL: reading and messaging chats on this host is the explicitly
approved authority. Legacy terminal credentials keep their existing authority.
Revocation rejects subsequent calls; already accepted work continues.

Anonymous pairing is bounded in memory and rate limited. Polling requires a
256-bit proof; browser URLs cannot redeem credentials. Denied, expired, revoked,
and invalid requests do not return a token. Responses are never cached; approval
pages have no external resources and suppress cross-origin referrers. Old hosts return a clear
unsupported result; existing manual-token connections continue to work unchanged.


## Consumers

[Desktop clients](desktop-clients.md) use this platform-neutral approval seam.
Amplifier for Mac stores the credential in Keychain; a future Windows client owns
its OS credential-storage implementation. Neither platform name nor app bundle
identity enlarges `companion-v1`. This guide remains the authority for pairing;
platform contracts reference it instead of copying the protocol.

## Credential and client isolation

Companion credentials use the distinct `amc_` family. Existing `amt_` terminal
credentials retain their full authority. Hosts predating pairing reject `amc_`
credentials even if their device store is retained during manual recovery.

Each native client ID is durably bound to the approving device. Attachment,
resumption and every header/query client selection reject identities owned by
another device or browser. Missing attachment is not permission to use shared UI
state. New native clients do not migrate legacy browser drafts.

Scope controls response bodies as well as allowed operations. Attachment never
returns browser state; create/send return only bounded receipts. Errors do not
include browser snapshots or unfiltered receipts, regardless of the requested
state transport. Revocation observation starts before reading a request body;
commands not yet admitted are rejected or disconnected when access is removed.
A shielded HTTP dispatch carries a revocable admission check through history,
configuration and lock waits. The service checks before creation preparation and
again before accepting new effects under its mutation lock. Receipt replay and
work already durably accepted are preserved.

The `device-pairing` scenario in `tests/web/browser-checks.json` owns browser
sign-in, approval and revocation acceptance. It provisions and cleans its own
synthetic fixture and is part of the standard browser-checks smoke plan.
