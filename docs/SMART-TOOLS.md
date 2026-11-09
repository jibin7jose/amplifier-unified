# Smart Tools and MCP Apps

Unified adds first-class support for optional Smart Tool adapters while retaining its
AmplifierSession, bundles, worker lanes, chat, notifications, and loop-live voice flow.
The host contains no imports, routes, or special cases for individual Smart Tools.

## Add a tool

1. Open **Settings → Smart Tools**. Browse the community catalog or
   paste a credential-free HTTPS Git repository URL into **Add from Git**.
2. Inspect the source. The descriptor and manifest are read from one resolved commit;
   inspection does not run a tool or interpret install instructions as commands.
3. Install the package when ready. This first installer supports Python projects;
   optional package extras such as `mcp` can be supplied. Each commit/extras combination
   gets its own environment. Other runtimes can be installed with their documented
   setup and then registered as MCP connections.
4. Add a local MCP connection using the tool's documented executable and argument array,
   or choose a remote Streamable HTTP endpoint.
   Each argument occupies one line. Set its retained-data directory explicitly.
   Environment entries map child variable names to existing host variable names,
   for example `OPENAI_API_KEY=MY_TOOL_KEY`; the form never takes key values.
5. Connect. Unified discovers compact tool descriptions and optional views. Full schemas
   load only when a user opens a tool or an agent requests named schemas. A catalog
   entry, an installed package, and a connected server are different states.
6. Run a tool through its schema-derived form, or ask the conversation agent to do it.
   For a tool advertising an MCP App, **Open interactive view** sends the latest
   completed call from this chat into a new canvas tab. No launch call is replayed.

Tool-owned AI does not automatically use the chat's model or provider credentials.
Configure generation using that tool's documented setup and bounded grants. For
example, an adapter that requires `--model-env` still requires that explicit option
and the intended environment references. Deterministic inspection, review, and
export need not invoke a model.

## Shared actions and state

The UI, `window.amplifier`, and agent `app_control` use the same actions:

- `smartTools.catalog`, `inspect`, `install`, `configure`, `connect`, `reconnect`, `disconnect`, `remove`, `uninstall`.
- `smartTools.call {id,name,arguments,catalogRevision?,sessionId?}` returns an `operationId` receipt.
- `smartTools.resources {id,kind?,cursor?}` lists resources or templates one page
  at a time; `smartTools.readResource {id,uri}` reads through that server.
- `smartTools.open {id,tool,operationId?,sessionId?}` attaches a standard view.
- `smartTools.viewStatus {canvasId,clientId?}` inspects the active view's saved
  source and live connection independently. It does not connect or call a tool.
- `smartTools.reconnectView {canvasId,expectedBindingRevision,clientId?,reviewedContract?}`
  reconnects that same tab through the ordinary connection/account checks. Read
  `viewStatus` first and use its exact binding revision. Agent requests are scoped
  to the calling chat; multiple matching clients require an explicit client ID.
- `smartTools.readResult {operationId,path?,offset?,limit?,operationRevision?}` reads
  a retained receipt synchronously, without creating another operation or changing
  a shared inspection slot. Follow `$operationPath` previews with the same operation
  ID and a relative JSON Pointer (for example `/result/structuredContent`). Continue
  pages with `nextOffset` and the returned `operationRevision`; changed receipts
  reject stale pages. `format: "json"` pages the whole original operation without
  a path. Oversized object keys automatically use this lossless fallback so keys
  cannot create unreadable pointers or unbounded previews.
  Receipt IDs remain valid after the recent-operation list rolls
  over, until configured result retention removes them. Agent action responses are
  compact receipts; read full app state separately when needed.
- The legacy `smartTools.result {operationId}` action still exposes a receipt at
  `/smartTools/inspectedOperation` for older clients. That shared slot and indices in
  `/smartTools/operations` are not stable identities for parallel agent reads.
- `smartTools.appCall` is scoped to the current canvas's server and granted tool names.
  Agent callers still obey model visibility; views obey app visibility.
- `smartTools.context` records bounded view context. It is observation, not an instruction
  or permission to generate, and does not automatically start a conversation turn.

Server instructions, compact tool summaries and lifecycle state are under `/smartTools/servers`.
Use `smartTools.discover {id,query?,limit?,offset?,refresh?}` for relevant summaries, then
`smartTools.schemas {id,names,catalogRevision}` for one to five exact callable definitions
(up to 64 KB). Pass that revision to the subsequent call. Canonical schemas remain
private to the connection; `loadedSchemas` contains only the most recently requested
bounded selection. Discovery returns at most 25 summaries and 16 KB, with `nextOffset`.
Server tool-list notifications, reconnect and reconfiguration invalidate loaded schemas;
a stale catalog blocks calls until explicit refresh. Revision checks run again when a
queued call reaches the transport. Legacy calls without a revision remain supported
against the current canonical catalog, with the same input and visibility validation.
The UI and agent schema requests do not connect a server or start a model turn.
Interactive views use a scoped HTTP wait for their normal `smartTools.appCall`
action, so completed calls return immediately without a browser polling delay.
The same admission, tool visibility, retained receipt and request-ID deduplication
apply. Closing the HTTP request never cancels or replays an admitted tool call.
Interactive calls commit admission and operation receipts immediately, while batching
full application-view persistence and broadcasts. Explicit state reads and revision
checks flush pending view updates; a crash preserves completed receipts and marks
unfinished work interrupted rather than replaying it.
Routine tool interaction does not toggle the host's rendering indicator; the
tool owns its progress controls, while errors remain visible in the host.
MCP App frames stay mounted while the canvas or Library hides them, preserving
local input. The host sends the optional boolean host-context extension
`com.microsoft.amplifier/visibility`, combining canvas and document visibility,
so supporting tools can pause automatic reads and resume one refresh. This is a
namespaced host extension, not a standard MCP field. Explicitly read-only,
non-destructive tool calls share identical in-flight reads and use a bounded
per-view lane (two running, four queued). Hidden or overloaded reads fail before
submission. Unknown tools and mutations retain distinct request IDs and do not
enter that lane; accepted work is never replayed or cancelled by hiding a frame.
Small operation receipts persist in SQLite. Full results are retained for at most
30 days, 200 completed operations and 32 MB (whichever limit comes first), in
pageable artifact files. Expiry keeps the execution receipt and never replays work.
Saved canvas artifacts remain reachable independently of result retention; static
HTML is stored once even when live view context changes. Failed operations produce the same acknowledgeable
attention indicators used elsewhere in Settings. A request accepted by the host is
not proof of domain completion: tools may return their own durable operation handles.

App backups include tool work stored under `smart-tools/work` in the app data directory. Tools configured to retain work elsewhere need their own backup; a canvas snapshot is not a complete tool-store backup.

Each canvas artifact saves its HTML, server configuration binding, launch operation,
and observed context in the chat's artifact library. The tool owns live work, immutable
revision IDs, drafts, and conflict rules. `updateModelContext` does not itself promise
view restoration; domain drafts should be persisted through the tool's public API.
Closing a tab hides it without canceling work. Reopening retrieves saved input/result
and reconnects presentation. After host restart, connections stay disconnected until
explicitly connected; uncertain work is marked interrupted and never replayed.

A fork copies a reference to the same tool work, not a tool-specific clone. Forked
views display a shared-work notice. Ask the tool to clone if it exposes that capability.
Updating a server configuration invalidates its old view bindings and launch receipts;
open a fresh view after reconnecting.

### Saved documents and reconnection

#### Presentation identity and display labels

The result extension `_meta["amplifier/presentationId"]` is an explicit tool-owned
entity identity, not a heading or an authority grant. An intentional
`smartTools.open` for the same identity navigates to its existing Canvas document
only within the same chat/workspace and compatible server configuration, account,
app resource, granted tools/schemas, permissions and CSP. A changed definition
advances Latest as an immutable version, subject to the existing dirty-view guards.
Different identities, incompatible bindings, and different operations without
an explicit identity open separate deliberate tabs; they never replace or erase
an independent artifact. Matching titles do not merge documents. See the
[Canvas identity and version guide](canvas.md) for saved-document semantics.

Each newly opened tool document leads its caption with a short opaque reference,
for example `[1a2b3c4d] Counter`. This is a prefix of its first persisted host
artifact ID, never its tool entity ID, presentation key, account, credential or
store path. Compatible reuse retains that original reference across methods,
operations and title/version changes. A colliding new reference extends its ID
prefix deterministically without relabeling older documents or history. Putting
the reference first keeps ordinary same-titled tabs distinguishable before the
230px tab caption ellipsizes. The Canvas presentation browser check verifies the
visible prefix, compatible reuse, independent tabs, and reload without replay.
The complete caption is bounded to 200 Unicode code points.

An optional display-only result extension `_meta["amplifier/presentationTitle"]`
supplies a literal human caption. It must be a string of 1–160 Unicode code points
after trimming. C0/C1 controls (including DEL) and bidi-format characters
U+200E/U+200F, U+202A–U+202E and U+2066–U+2069 are rejected; ordinary RTL script
is allowed. Invalid, empty, oversized or malformed metadata falls back to a safe
tool title/server name, or `MCP App`, without failing or altering the retained
domain result. HTML, Markdown and URLs in captions are untrusted text, not markup,
navigation, instructions or permissions. The host never parses a domain JSON
title and never trims or normalizes `presentationId`.

Title updates create new saved definitions without rewriting old titles, bodies,
results or exact-version links. Reopening the same unchanged operation/result is
idempotent after any initial label upgrade; old definitions are not backfilled.
Display labels add no calls, grants or automatic reconnect/reload behavior.
Tool-owned draft restoration and authored initialization/no-replay remain separate
acceptance obligations: host labels and synthetic fixtures do not qualify the
actual Stories, Possibly, Unfold, tmux or Design Studio receiving contracts.

Saved MCP HTML remains readable inside its existing sandbox after disconnect,
restart or a changed server configuration. Live tool and resource calls still
require a valid binding. Missing source is reported separately, retaining its
original reference; a handshake timeout does not imply that the source was lost.
The tool's own server/state may still be needed for live features.

New views record a fingerprint of their granted tool definitions and launcher,
plus any attested account identity. Discovery generations may change on restart;
an explicit reconnect can rebind an unchanged contract without reading today's
HTML, adding tools, creating another tab or reissuing earlier calls. Changed
configuration, account identity, app resource or granted schemas fail visibly.
The reconnect control preserves the mounted iframe and unfinished input. Reload
is a separate explicit control because it resets local inputs and reruns the
authored document's initialization code.

An initialized MCP App receives the SDK's `notifications/tools/list_changed` and
`notifications/resources/list_changed` when its host binding moves from a
non-ready state to verified ready. Repeated ready observations do not repeat the
notifications. Discovery stays limited to the saved grants. A cooperating app
can refresh its safe reads on that signal; the host never repeats initialization
input, a prior tool result or a mutation. This does not repair an app that ignores
the notifications, and it does not make a deliberately selected saved version live.

Agent `smartTools.viewStatus` is passive and scoped to the calling conversation.
It distinguishes no connected client, an unavailable supplied client, multiple
connected clients, and a document not selected by the matching client. Persisted
disconnected clients do not count as live viewers. Without a selected live view,
the receipt still reports the latest saved source and binding state under
`savedView`; it does not select a tab, attach a client or connect a server.

Older views lack a saved contract fingerprint. Reconnect returns `review_required`
with the current definitions for the already-granted action names. After reviewing
them, the user or authorized agent can supply that exact `review.fingerprint` as
`reviewedContract`. The binding is checked again before committing. This does not
grant newly discovered tools or infer a missing historical account identity.
Legacy views with an attested but unrecorded account require a newly reviewed view.
This reconnect path does not replace document history or merge tabs by title.

For changed definitions, the human-only **Review tool changes** flow shows a
bounded comparison with the saved definitions. Older bindings explicitly say
when the previous definitions are unavailable. Description changes require
review; added actions remain ungranted. Configuration/account changes and a
changed app resource identity remain separate blocked cases. An exact review is
bound to the reviewing client and its selection generation.

**Approve existing actions** keeps the mounted document and unfinished inputs.
**Open updated live view** additionally requires an explicit input-reset
acknowledgement, saves current resource HTML as a new version of the same artifact,
and retains its launch arguments/result and board identity without calling the
launcher. Other mounted clients preserve their actual old iframe and local input,
become read-only immediately, and choose **Open live view** when ready. Historical
selection also exposes that action directly. See the
[saved MCP contract review contract](clients/mcp-contract-review.md) and
[October17 qualification](validation/mcp-contract-review-20261017.md).

Local acceptance: `npm --prefix frontend run test:mcp-recovery-browser` uses an
actual MCP SDK fixture and restarts the host over isolated synthetic state. It
checks source identity, launch receipts, mutation counts, input preservation,
cooperative safe-read recovery, deliberate historical selection and changed-schema
denial. It also runs in the nightly or explicitly dispatched extended browser plan
against the selected immutable published revision, not automatically per release.
`AMPLIFIER_TEST_PYTHON` selects the test interpreter. See the
[807–809 qualification and remaining decision](validation/mcp-recovery-807-809.md).

## Supported protocol profile

Uses the official Python MCP SDK and official MCP Apps `AppBridge`, not a new protocol.
The client advertises `io.modelcontextprotocol/ui` with `text/html;profile=mcp-app`.
Tools advertise `_meta.ui.resourceUri`; the host reads the corresponding `ui://`
resource. UI permission/CSP requests are read from the resource's metadata.

This initial host profile supports:

- Local stdio and remote Streamable HTTP MCP servers with typed discovery, calls and UI resource reads.
- SDK OAuth authorization-code flow with PKCE, resource/issuer validation and token refresh; see [connector lifecycle](CONNECTORS.md).
- Legacy tool-list notifications and modern SDK `subscriptions/listen` invalidate cached definitions.
- Self-contained MCP App HTML; scripts/styles are inline, images/media are embedded,
  and nested generated previews may use isolated blob frames.
- Scoped `tools/list`, `tools/call`, and text/structured `ui/update-model-context`.
- Standard `resources/list`, `resources/templates/list`, and `resources/read`
  through the view's saved server binding. The host never fetches resource URIs
  itself. Encoded responses are limited to 2 MB; large media uses tool-defined
  bounded chunk resources assembled into blob URLs by the App. Domain resource
  authorization belongs to the configured server. Closed views and changed
  connections cannot continue reads. Media reads do not bloat chat history.
- Explicit environment references, input validation, source-window checks, opaque
  iframe origins, stale-configuration checks, and recorded results.
- User-clicked downloads from tool views. Other device permissions and network/asset
  domains are denied and the granted sandbox profile is advertised to the view.

Legacy SSE transports, host sampling, elicitation, MCP Tasks,
resource subscriptions, and external UI asset/network grants are not implemented.
A server's native operation handle is not claimed to be an MCP Task. Model costs
remain server-provided data; missing usage is unknown, not zero, and is not rolled
into chat spending as if independently metered by Unified.

The installer executes a package build only on an explicit install action. Connecting
executes a configured local process or contacts the configured remote endpoint. The app scopes iframe access; it is not an OS sandbox
for installed code. Tool metadata and rendered content remain untrusted data.

## Validation

`pytest tests/test_smart_tools.py tests/test_smart_canvas.py tests/test_connector_lifecycle.py` covers real SDK subprocess
calls, visibility, environment isolation, schema/resource validation, timeouts,
configuration races, restart and duplicate receipts, durable paging, and canvas bindings.
`npm --prefix frontend run test:smart-tools-browser` uses an unfamiliar independent
MCP App and the real host to verify rendering, shared state, nested-message isolation,
reopen behavior, and stale configuration failures without provider calls.

Possibly's optional adapter is tested separately, including against an independent
MCP Apps host. Tool-specific integration tests live with that adapter or in development
reports, keeping Unified's production implementation generic. The collaborative-surface
proposal remains a discussion draft, not an adopted standard or conformance claim.

## Disconnected canvas requests and activity

A disconnected canvas rejects new tool requests before admission. Reconnecting
remains explicit and does not resend earlier requests. Calls admitted before a
connection change keep their durable receipts, including uncertain outcomes.

Activity groups equivalent host connection refusals for the same saved connection
and configuration, with a count of the recent records in its bounded overview.
The original operation records remain individually inspectable. Tool-returned
errors, timeouts and interruptions retain their own attention entries. Reviewing
a group acknowledges only the exact membership shown; another failure makes it
unread again. Historical individual acknowledgments remain respected.

Saved MCP views refresh their connection status when the shared server changes
or the page returns to the foreground. This is a read-only availability check: it
does not reload the frame, discard unfinished input, reconnect automatically, or
replay the launch tool. Reconnect remains an explicit user/agent action.

`frontend/tests/mcp-clients-browser.mjs` exercises independent desktop and mobile-
sized clients, stale status replies, foreground return and cross-client reconnect.
The extended browser runner owns it as `mcp-clients`, alongside `mcp-recovery`.
Its empty-account fixture waits for completed connection discovery and dismisses
the ordinary setup dialog before testing the saved tool; it does not hide the
dialog or suppress discovery. Launch completion is read from its exact durable
operation receipt because the compact client state omits operation collections.
It uses a synthetic counter server and proves no replay; mobile emulation does
not substitute for acceptance on a physical phone/browser.
