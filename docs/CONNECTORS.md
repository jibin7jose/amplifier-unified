# MCP connection and account lifecycle

Smart Tools remains the registration, installation and operation-receipt authority.
This extension uses the official Python MCP client, transports and OAuth provider.
It adds no connector catalog, brand adapters, scheduler, tool execution bypass or
automatic model-context injection. Provider configuration remains with the existing
provider stack; it is not copied into tool connections.

## Connection setup

`smartTools.configure` saves a local `stdio` executable or `streamable-http` URL.
Saving, catalog browsing, schema reads and opening Settings never connect. Remote
URLs require HTTPS, except loopback HTTP for local servers. URLs may not carry
credentials, queries or fragments. SDK origin-safe redirects remain enforced.

For local processes, `env` maps child variable names to host environment variable
names. For remote servers, `headers` maps HTTP header names to host environment
variable names. Values stay on the host, outside configuration, browser state and
operation receipts. A variable referenced by `Authorization` must contain the full
header value, including its scheme. Protocol-controlled headers and cookies cannot
be overridden. No unrelated environment credentials are forwarded.

Choose `auth: "oauth"` for a remote server supporting MCP OAuth discovery and client
registration. Choose `auth: "environment"` for documented credential headers or a
server requiring no authentication. The host does not implement its own OAuth
exchange or guess brand-specific endpoints. Servers without supported discovery or
registration need their documented header configuration; pre-registered client IDs,
enterprise assertion grants and legacy SSE are not exposed by this profile.

## Shared actions

All controls use the same `app_control` action definitions and durable Smart Tool
operation receipts as the UI. An accepted action is not proof that authorization or
tool-owned work completed.

| Action | Behavior |
| --- | --- |
| `smartTools.connect {id}` | Connect using current configuration and saved authorization. Never opens an interactive login. |
| `smartTools.reconnect {id}` | Close and explicitly establish a new connection. No prior tool calls are replayed. |
| `smartTools.disconnect {id}` | Cancel a pending login and close the transport. Keep saved authorization. Remote work may continue. |
| `smartTools.authStart {id,redirectOrigin?}` | Start/check a bounded SDK authorization flow. Returns initial progress immediately; the user opens its authorization link and completes provider consent. |
| `smartTools.authStatus {id}` | Read progress without connecting. The same progress is in the server's `login` field. |
| `smartTools.authCancel {id}` | Cancel pending sign-in. Does not revoke an existing remote grant. |
| `smartTools.authForget {id}` | Disconnect and remove local OAuth tokens/client registration. Reports remote revocation as not attempted. |
| `smartTools.remove {id}` | Forget credentials and remove a connection registration. Keep its installed package and external tool work. |
| `smartTools.uninstall {id}` | Remove an app-managed installation only after dependent registrations are removed. Deletes its installation folder; work stored elsewhere is untouched. |
| `smartTools.discover {id,query?,offset?,limit?,refresh?}` | Search bounded visible summaries; explicit refresh re-lists the catalog. |
| `smartTools.schemas {id,names,catalogRevision}` | Load one to five exact schemas from the reviewed catalog, up to 64 KB. |

Use the installation's `installationId` when registering its executable. Uninstall
also detects absolute executable, argument and working-directory paths into an
installation. It refuses symlinked installation folders and never removes arbitrary
paths. Store tool data outside `smart-tools/installs`; `smart-tools/work` is an
appropriate app-backed location. External runtimes are managed by their own installer.

## Truthful state and authorization

`connectionState` is `disconnected`, `connecting`, `auth-required`, `ready`, `account-review` or `error`.
The older `status: connected` remains for compatibility when `connectionState` is
`ready`. `lastVerifiedAt` records the last completed connection handshake. Readiness
describes the current client connection, not a guarantee about a remote service's
future availability. Observed transport closure invalidates the catalog and reports
disconnection immediately. Unified does not activate saved connections in the background
or poll for health. Within an explicitly active HTTP session, the SDK may resume a
response/event stream using Last-Event-ID; this does not replay a tool call or activate
a disconnected registration.
Host restart marks connections disconnected and discards cached callable schemas.

`account.status` is `unknown` unless the connector supports the explicit
[authenticated account resource contract](CONNECTOR-IDENTITY.md). Supported OAuth
connections bind the resource's exact issuer and subject; `verified` means
**server-attested through authenticated transport**, not independently verified
legal or human identity. A mismatch becomes `changed` and blocks dispatch until
an exact `smartTools.accountAccept` receipt is followed by explicit reconnect.
Previously bound but unavailable identity is `unconfirmed` and also blocks dispatch.
Ordinary MCP metadata and opaque OAuth access tokens do not establish identity.
The host never infers account names from server titles or decodes tokens. Requested
scopes and consent progress are separate from scopes actually returned in the token
response; absent granted scopes are `null`, not an empty grant. Header authentication
cannot establish that provider consent was granted. A 401/403 reports authorization
required and bounded advertised challenge scopes; it does not trigger a browser flow.

OAuth progress lasts at most ten minutes. The authorization URL uses SDK-generated
state, PKCE S256 and the SDK's resource/issuer binding. The callback origin must be
one of the configured app origins (HTTPS for a remote host). The public callback
only accepts the exact pending, unexpired, one-time state and origin. It never accepts
pasted authorization codes through action arguments or the command journal. A wrong,
duplicate or expired callback is rejected. After receipt, the browser is redirected
to a fixed clean completion URL; a completion page says only that a response arrived.
Provider denial or a later token/connection failure remains an error in the UI.

Credentials are stored with the existing atomic private-file writer under
`smart-tools/credentials`, mode 0600 with a mode 0700 directory. Files are bound to
the exact connection configuration and never published in app state. They are local
private files, not OS-keychain encryption; normal private app backups include them.
Endpoint, executable or authorization configuration changes and removal cancel pending login and discard previous local
credentials; a display-name change preserves them. The SDK owns protocol discovery, registration, authorization, refresh,
PKCE, issuer checks and resource validation. With tested MCP 2.2.0, the host also
persists/restores SDK discovery context and absolute expiry: TokenStorage alone does
not preserve those fields across a restart. This prevents extending a token's lifetime
or guessing a different authorization server's refresh endpoint after restart.

The production CLI access logger omits URL queries and Referer values, including
callback codes. Reverse proxies must apply their own equivalent log policy. Callback
responses are not cached. SDK wire/authorization diagnostics are scrubbed within these
connection tasks so validation exceptions cannot print unpersisted credential values. Forgetting local sign-in cannot revoke provider-side access;
use the provider's account controls for that. No test connects a real user account.

## Progressive schemas and cancellation

Canonical schemas are scoped to the live connection and capped at 500 tools,
20 pages and 1 MB. Public summaries omit input/output schemas. Search returns at most
25 summaries or 16 KB, with an explicit next offset; the most recent schema selection
replaces the preceding selection in browser state. Retained operation receipts use
the existing bounded artifact/paging store. Model-visible discovery excludes app-only
tools; existing call visibility and JSON Schema validation still apply.

Every refresh gets a new catalog revision. Legacy notifications and modern SDK
subscriptions invalidate loaded schemas without fetching new ones. Reconnect and
reconfiguration do likewise. Calls with a stale revision fail before dispatch; the
transport checks the catalog epoch again after earlier queued work finishes. Updated
MCP App views bind their catalog revision as well as their connection configuration.
Without advertised changes, the host cannot know that a remote server silently changed
its schema; explicit refresh/reconnect re-lists it. Old direct calls without a revision
remain compatible but use the current canonical schema, never a stale catalog.

The SDK request owner retains its serial queue and transport lifetime. Cancellation
or timeout closes the client and records interruption/unconfirmed outcome. It is not
proof that a remote mutation stopped or rolled back. Queued work is never replayed.

## Executable acceptance

`pytest tests/test_connector_identity.py tests/test_connector_lifecycle.py tests/test_smart_tools.py
tests/test_smart_canvas.py tests/test_auth.py tests/test_server_auth.py` exercises
real local MCP stdio/HTTP, official SDK authorization/token endpoints, browser consent
fixture, PKCE/resource checks, refresh, denial, cancellation, wrong/replayed callback,
private storage, missing/invalid credentials, visibility, schema bounds, stale queued
calls, abrupt child death, package dependencies and existing app authentication.

`npm --prefix tests/web run test:connectors-browser` starts the production aiohttp host
and production assets with a local MCP/OAuth fixture. It checks configure-without-connect,
explicit consent, user and agent calls, progressive schema loading, stale-call denial,
reconnect, forget/cancel, desktop/mobile layout and preserved conversation/draft.
`npm --prefix tests/web run test:smart-tools-browser` retains the MCP Apps regression.
Real-account interoperability, provider-specific consent screens and remote HTTPS
deployments remain untested; this is not a brand conformance claim.
