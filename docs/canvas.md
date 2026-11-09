# Workspaces and agent canvas

Assistant replies can open workspace files directly in Canvas. Local Markdown
links, inline-code filenames, and plain paths with a separator and file extension
use the shared `canvas.openFile` action. It binds the original chat and workspace,
checks the existing file-preview boundaries on click, and preserves the message
draft. Missing files and paths outside the workspace show a small notice beside
the reference. Normal web links and fenced code blocks keep their existing behavior.

Recognition currently covers POSIX paths, including `:line[:column]` suffixes;
the suffix opens the file but does not scroll to a source line. Rendering does not
scan the filesystem or submit conversation work. Agents can use the same action
with `sessionId`, `workspace`, `path`, and `clientId` when multiple clients show the
chat. Unsaved Canvas edits retain their existing protection.

Workspace registrations are durable app state. `workspace.add` accepts an existing
folder and optional name; `workspace.select`, `workspace.rename`, and
`workspace.remove` operate on its ID. Selection sets the new-chat default.
Removing a registration never deletes its folder or conversations. Keep at least
one workspace registered. `workspaces` and `selectedWorkspaceId` appear in the
same state visible to the agent and user.

## Settings as a Canvas item

Use `canvas.show {"kind":"settings"}` to open live Settings beside the calling
chat. This creates an ordinary chat-owned tab, or reopens that chat's existing
Settings tab. It has no permanent shortcut in the Canvas header. Users can
switch, close, and reopen it through the same tabs and saved-items list as other
artifacts. Only the live-view identity is stored; settings values and credentials
are never saved in the artifact body.

Navigate pages with `view.update {"patch":{"settingsExpanded":["appearance"]}}`.
Read `settingsUI` and `visibleUI` to inspect the current route and non-secret
controls, and use the existing typed settings actions to change preferences.
Private credential entry and unsaved-change protection stay in the Settings UI.
The earlier `view.update` `settingsCanvas:true` route remains compatible and now
opens this artifact. Assisted setup sets the initial split once; later user
resizing persists normally.

## Workspace layout

**Context inspector** is another native chat-owned live tab, opened from Chat
overview or Chat info through `canvas.show {"kind":"context-inspector"}`. It uses
the same panel resizing, focus, tabs and close controls as documents. Only its
identity is saved in the Canvas library, not captured prompt bodies. Its
[request evidence and privacy contract](clients/context-inspector.md) remains
independent of Canvas presentation. It accepts no supplied content, path or URL.

Drag the canvas's left divider or pinned navigation's right divider. Chat keeps
360 pixels, canvas keeps 300, and pinned navigation keeps 216; the pane being
resized gets the remaining room. Keyboard users can focus a divider and use
arrows (20 pixels), Home (minimum), or End (maximum). On narrow windows, the
canvas covers the workspace instead of squeezing both panes.

Canvas has one document navigation row: **Overview** and the open documents. Overview
stays at the leading edge while the document strip scrolls; its label contracts
to an icon without resizing the strip. **Open documents** offers the same open
items in a bounded, searchable list, with named close actions. Each tab also has
an always-visible **Close tab [title]** button beside its title. Closing a tab
keeps its saved output. Arrow keys move tab focus; Enter selects and Delete closes
a focused document tab.

The artifact's compact action row keeps version identity, Copy, Download and
**Details** together. Details retains exact-version, renderer, source and
format-specific controls. Images, PDFs and diagrams retain their dedicated tools.
Rendering failures stay visible when Details is closed. HTML, browser and MCP
App views retain their unpadded content area; documents retain readable margins.

Every present Overview section is collapsible. Counts, status and attention
remain in its heading. Section choices, output search and reading positions stay
in the originating browser tab, scoped to the chat, across document visits and
Canvas hide/reopen. The overview has one bounded scroll region through Publishing;
Outputs is not a second nested scroller. The container-header Canvas button hides
or shows the panel without closing the selected artifact or discarding edits.
The labeled **Close Canvas** button in the Canvas header uses that same visibility
operation to return to chat, preserving open tabs, saved versions and unsaved
viewer state. Narrow panes place the header actions below the document strip so
close controls keep their 44px targets. Reopen with the container-header Canvas
button; reopen a closed document from Overview's saved outputs.

The short Overview-label transition respects `prefers-reduced-motion`, including
runtime changes. This revision has no separate app-level Less motion setting.

**Focus canvas** fills the space below the container header; **Exit canvas focus** returns to the split
layout. Escape also exits when focus is in the host UI (embedded apps may consume
keyboard events). Resizing and focusing keep the current viewer mounted; they do
not reload its content or reconnect its tool server. Closing or changing tabs
still follows the viewer's normal lifecycle. The container-header Canvas toggle
remains reachable in focused and narrow views. A nested dialog handles Escape
before the focused Canvas does.

All layout controls use `view.update` and are available to agents:

| Patch field | Meaning |
| --- | --- |
| `canvasWidth` | Preferred width, 300–16384 pixels; fitted to available room |
| `navWidth` | Preferred pinned navigation width, 216–16384 pixels |
| `navPinned`, `navExpanded` | Keep navigation open or reveal it temporarily |
| `canvasFocused` | Full-frame view; false restores the split layout |
| `canvasControlsPinned`, `canvasControlsExpanded` | Keep controls visible or reveal temporarily |

Preferences persist in `/view`; browser snapshots in `/devices` include actual
pane bounds and divider minimum/maximum/current widths. Closing the canvas clears
full-frame mode while retaining preferred split widths.

## Agent awareness and access

### One full presentation per saved version

`canvas.show` saves content and chooses `presentation: "canvas"` by default.
The originating turn keeps a compact exact-version card, not another executing
preview. Use `"inline"` for a short review in chat without replacing unrelated
Canvas work, or `"saved"` for the compact card only. Inline needs an attached
client displaying the chat, an originating publication turn, and a supported
kind. PDF, browser, Settings, A2UI and app viewers remain Canvas-only.

`canvas.select {id, version, presentation, messageId}` changes the presentation
of existing saved content; inline requires its exact publication `messageId` or
`questionId` plus immutable `questionContentRevision`, never both. Question
references validate their retained session, version and body identity before any
presentation changes. The same source binding fences late inline mounts.
It never creates another artifact, revises content or starts conversation work.
The choice belongs to this client and exact artifact version, survives reload,
and does not change another client's choice. Old calls without `presentation`
still open Canvas. Generated-image receipts retain their inline default unless
the user chooses Canvas or hides the preview.

The card keeps its title, type and version while **Preview in chat** / **Hide
preview** toggles its body. Opening Canvas does not force full-frame focus.
Dirty-view refusals keep the current renderer and show the error beside the
card. Ordinary HTML/3D has no lossless state-transfer protocol: moving a mounted
frame requires the user's explicit **Reset preview** choice, or **Keep current
preview** preserves it. Reset never bypasses a declared dirty edit. Closing the
Canvas panel alone retains its existing view; it does not request inline display.
`canvas.views.inline` is the lower-level renderer-lease API, not a card-expansion
action. No CSS-hidden second executing renderer satisfies the one-surface rule.

### Artifact controls and reuse

This is a bounded control inventory, not a new renderer registry or design system.
The shell's extension slots remain a separate contract. Before adding artifact
controls, reuse the owning entry or record the necessary semantic difference.

| Control / owner | Variants, state and reuse boundary | Accessibility / focused evidence |
| --- | --- | --- |
| Document close and panel dismissal: `frontend/src/canvas-library.jsx` (`CanvasTabs.remove`), `shell-panels.jsx`, `canvas-experience.css` | The tab's visible close button and Open documents list share guarded `canvas.tabClose`; saved artifacts/versions remain. Labeled Close Canvas and the container-header toggle share `canvas.visibility`, retaining mounted viewers and open tabs. No new state owner or deletion action. | Named native buttons, 44px targets, keyboard focus and refused-close feedback; `canvas-close-browser.mjs`, `canvas-dirty-browser.mjs`, `canvas-overview.test.mjs`, `shell-panels.test.mjs`. |
| Compact exact-version card and disclosure: `frontend/src/inline-artifacts.jsx` (`DocumentOutput`, `Preview`, `ArtifactLinks`), shared `artifact-file-details.jsx` | HTML, documents, diagrams and images share title/type/version/open/disclosure; image gallery and specialized renderer bodies remain distinct. File details reads the exact saved definition without mounting a preview: Copy full path and Copy relative path copy plain provenance paths, relative to the conversation's current working folder with its base shown. Saved-only artifacts explicitly have no backing path; outside-base paths have no relative copy. Download fetches retained bytes with visible refusal/start feedback, never executes HTML. Client-local placement, pending/error and explicit HTML reset are not generic card state. | Native disclosure/buttons, 44px targets, shared clipboard completion/error feedback and exact-version download; `tests/test_canvas_paths.py`, `tests/test_canvas_downloads.py`, `frontend/tests/generated-file-controls-browser.mjs`, plus `tests/test_artifact_presentation.py` and `frontend/tests/artifact-one-surface-browser.mjs`. |
| Viewer controls: `frontend/src/canvas-controls.jsx`, `canvas-viewer.jsx`, `canvas-workspace.jsx`, shared `artifact-file-details.jsx` | Existing renderer host, exact source binding, source/copy/download, portal placement and inline toolbar suppression. The primary Download action and File details reuse the same saved-version download owner as chat cards, bound to the running definition when an executable preview retains an older version; original-file reveal keeps its existing host/view guard. Refresh uses `canvas.views.inspect`; executable previews retain their running definition while discovering new versions. Explicit UI-only `canvas.views.loadVersion` binds both the outgoing view and exact incoming version, with visible reset/code consequences and dirty protection. Current/historical/retained, pending, failed and unconfirmed feedback stays visible. Live apps and websites retain their controls. No competing viewer/registry. | Native named Refresh/Load/Download buttons, live status, 44px targets and keyboard/dirty guards; `tests/test_canvas_views.py`, `tests/test_canvas_versions.py`, `generated-file-controls-browser.mjs`, `frontend/tests/canvas-refresh-browser.mjs`, `canvas-refresh-catchup-browser.mjs`. |
| Canvas Details inspector: `frontend/src/shell-panels.jsx`, `canvas-experience.css` | The existing control portal stays mounted beside the reader at Canvas widths of 720px or more. Narrower panes use the same bounded right-hand sheet below the single navigation bar; only the covered reader is inert. The compact version/copy/download/Details row and format-specific viewers remain their existing owners. No new renderer or preference is introduced. | Named region, 44px controls, Escape/close focus restoration, retained header toggle and document navigation. `frontend/tests/shell-panels.test.mjs`, `canvas-chrome-browser.mjs` geometry/old-top-band negative and `canvas-dirty-browser.mjs` retained-editor checks. |
| Saved output inspection: `frontend/src/outputs.jsx`, shared `image-actions.jsx` | Snapshot read/download/lineage and image follow-up; output HTML is **not** an executable preview. See [Outputs](OUTPUTS.md). | Exact-target download and draft-preserving follow-up; `tests/test_output_images.py` and inline-artifact browser tests. |

When changing these owners, format routing, placement or action semantics,
update the affected entry (or state why its contract is unchanged) and extend its
focused test. HTML/Markdown parity checks must exercise the same disclosure and
exact-version open path, not merely compare CSS. This index and review discipline
do not claim universal control conformance.

The host mounts `app_control` and injects canvas guidance as ephemeral system
context on every provider request, including resumed conversations. Delegated
sessions inherit this access through the loop-live execution host. The voice
interface asks the same AmplifierSession to produce visual artifacts.

The guidance encourages visuals for architecture, comparisons, workflows and
interactive explanations when useful. It never treats document content as
instructions and asks delegated workers to coordinate use of the shared canvas. Each publication creates a saved artifact rather than discarding the previous one.

```json
{"operation":"dispatch","args":{"action":"canvas.show","args":{
  "kind":"mermaid","title":"One shared session",
  "content":"flowchart LR\n  Chat --> AmplifierSession\n  Voice --> AmplifierSession\n  AmplifierSession --> Canvas"
}}}
```

Call `app_control` with `operation:get_state` to see the canvas and
`operation:list_actions` with `args:{"prefix":"canvas."}` for canvas action schemas.
Read the returned `items` and follow `nextOffset` with the same prefix/revision;
see [bounded discovery](AGENT-RESULTS.md) for oversized-schema reads.
The default state read is a bounded overview of the calling conversation and
visible UI. Other conversations are listed by title/status, without their content.
Use `get_state` with `args:{"path":"/canvas","offset":0,"limit":50}` to inspect
one section. Large values have `$statePath` references; follow `nextOffset` for
more entries or text. Optional `revision` detects changes between pages.
For `/sessions` pages and indexed paths, versioned reads must also carry the
`directoryRevision` returned by the overview or a directory read. This token
identifies the local and native directory views used to assign those indices;
native discovery can change them before the next app revision is published.
A stale token rejects the read before resolving its index. Unversioned reads use
the current directory. Directory paging loads only the requested owners and
does not select chats or start work. The HTTP detail route accepts the same
`directoryRevision` query parameter.

The overview preserves global `(ordinal, id)` positions before selecting its
first 50 conversations and 20 exact-parent subagent entries. It counts those
subagents through indexed source candidates and applies the same canonical
parent and suppression rules as a complete directory read. Only the selected
owner and requested metadata rows are decoded; global ranking carries scalar
identity/order/kind fields rather than every historical summary and parent
projection. The rank still visits directory membership and is synchronous.
`directory.overview` records its duration in bounded performance diagnostics;
it records no read arguments, paths or conversation content.

Resolved configurations are stored once per session. Bulky dependency provenance
is stored once in the app database and referenced by `$resource`; requesting its
JSON Pointer path automatically loads a bounded page. The same read-only paging
is available at `/api/state/detail?path=...`. Existing module editing still uses
the canonical `sessions[i].configuration.plan`. Dispatch receipts use the compact
state overview rather than repeating the entire app state. `canvas.show` accepts:

| Kind | Preview |
| --- | --- |
| `browser` | Live HTTP(S) URL preview, with external-browser fallback |
| `html` | Self-contained HTML, inline CSS and interactive JavaScript |
| `markdown` | Markdown tables, lists, code and fenced `mermaid`, `dot` or `graphviz` diagrams |
| `mermaid` | Mermaid diagrams |
| `dot` | Graphviz diagrams, six layout engines and node attribute inspection |
| `json`, `jsonl` | Formatted records with filtering; first 200 matches shown |
| `code`, `text` | Highlighted code or plain text |
| `image` | Embedded PNG, JPEG, WebP or GIF |
| `auto` | Detect a workspace file's type from its extension |
| `a2ui` | Declarative component snapshot, described below |

Use `content` or a UTF-8 `path` inside the selected workspace. Symlinks may not
escape that folder. Inline content and ordinary text files are bounded to 1 MB,
images to 5 MB and diagrams to 50,000 characters. HTML and Babylon.js **files**
may be up to 20 MB (20,000,000 bytes). HTML over 1 MB is saved separately in the
artifact store and fetched only by the preview, source viewer, copy, or download.
It keeps the same opaque sandbox and does not inflate regular app-state updates.
For larger pages or apps with linked assets, serve them from a web server and use
a reachable HTTPS URL, or open the file separately in your browser. Graphviz runs in a terminable browser worker with a 15 second timeout;
Mermaid limits graph size. Both libraries are bundled: no CDN or Graphviz install
is needed. Graph SVG is sanitized and displayed as an inert image.

Preview/Source, diagram zoom/pan/fit, Graphviz layout/node selection and structured
data filtering use `canvas.view` and are visible to the agent. Copy/download use
`canvas.copy` and `canvas.download`, through the same browser effect mechanism
for agent and user actions. Clipboard availability depends on browser permissions.
Graph layout defaults can be overridden by authored DOT attributes.

`canvas.renderReports` records pending, ready, unverified or error for each preview/diagram
fence. The agent must distinguish a submitted artifact from a browser-confirmed
render. Replaced artifacts ignore late reports. Reports are display evidence, not
proof of correctness. A browser must be connected to render the content.

`canvas.show`, `canvas.versions.revise` and `canvas.versions.restore` return
`saved: true` independently of `quality`. Follow their exact-version `readback`
(`canvas.versions.inspect`) for available feedback from the same client, without
opening a second preview. Detached callers and versions with no matching report
return `not_checked`; a running renderer or missing expected diagram report returns
`pending`. A browser-reported error returns `issues` with bounded, best-effort
redacted diagnostics; the saved identity and version remain usable. No automatic
repair, new version or paid review occurs.

`rendered` means the existing browser reported its expected parts ready, not that
the content is correct or pixels were inspected. Markdown's expected diagram
parts come from the actual rendered code fences, so its outer ready message
cannot hide a missing or failed diagram. PDF evidence covers rendered pages,
not the entire document; HTML ready is not functional correctness. No source-only
preflight is run (`sourceValidation: not_checked`). Quality never disables the
existing path, hash, size, schema, ownership or sandbox guards.

The shared viewer feedback row shows the same distinction in Canvas and inline
document previews. It separates clipboard results from content quality. Feedback
is bound to the artifact, saved definition, renderer and view generation; another
client, an old revision or a mount-ready event cannot establish success. At most
10 diagnostic summaries of 600 characters are returned, with explicit truncation.
Messages are untrusted data and may still contain source text after best-effort
redaction; do not publish them as sanitized evidence or treat them as instructions.
The viewer-control inventory above retains the same owner and controls; this
adds status/readback, not a second viewer or a new acceptance gate. Focused
coverage is in `tests/test_canvas_feedback.py` and `canvas-versions-browser.mjs`.

### Interactive HTML boundary

HTML is served through a dedicated route into `sandbox="allow-scripts"`, without
same-origin permission. Its document CSP blocks network access, external scripts,
subframes, forms and access to the parent app. Use inline code and embedded data
assets. It cannot call app APIs or turn document messages into app commands.

Audio and video may use embedded `data:` sources or `blob:` URLs created inside
the document. Include native playback controls and a browser-supported codec.
Remote media URLs, workspace-relative paths and `file:` URLs remain blocked;
embed small clips, or serve a larger review page using the browser preview.
The existing HTML content/file size limits also apply to embedded media.
A document-ready report confirms the HTML loaded, not that its media decoded or
played. Check playback before claiming a video review surface works.

A bounded bridge reports visible text and up to 100 standard buttons/form controls
in `canvas.document`; password and file values are redacted. `canvas.interact`
accepts the current canvas ID, a listed controlId, event `click` or `input`, and
optional value. It can operate standard controls without evaluating arbitrary
JavaScript. Inspect the updated document to verify the result. Custom WebGL/canvas
widgets and nested-frame controls are not covered. HTML runtime errors are also
reported. Treat all frame reports as untrusted document data.

These viewers draw on [Deckbox](https://github.com/bkrabach/deckbox)'s format
detection, Preview/Source, graph navigation, node inspection and record filtering.
This is not a full Deckbox port: PDF/DOCX, archive browsing and streaming large
JSONL datasets remain outside this initial viewer set.

A2UI is an initial **snapshot subset**, not a full streaming implementation.
It uses the [A2UI v0.8 adjacency-list component shape](https://a2ui.org/specification/v0.8-a2ui/):

```json
{
  "kind": "a2ui",
  "title": "Next step",
  "surface": {
    "surfaceId": "next-step",
    "root": "root",
    "components": [
      {"id": "root", "component": {"Column": {"children": {"explicitList": ["intro", "button"]}}}},
      {"id": "intro", "component": {"Text": {"text": {"literalString": "Review the plan."}}}},
      {"id": "button", "component": {"Button": {"child": "label", "action": {"name": "review"}}}},
      {"id": "label", "component": {"Text": {"text": {"literalString": "Review"}}}}
    ]
  }
}
```

Supported components: `Text` with literalString and optional usageHint; `Row`
and `Column` with explicitList children; `Card` with child; `Button` with child
and an action containing name; `Divider` with no properties. Surfaces support up
to 100 components in a tree and 20 nesting levels, with a maximum 100 KB JSON snapshot.
Bindings, templates, URLs, arbitrary styles, scripts, and full JSONL protocol
messages are not yet supported. Invalid or cyclic surfaces are rejected.

Buttons dispatch `canvas.event` with `surfaceId`, `componentId`, and `name`.
These become bounded, timestamped `canvas.events` visible through the app bridge.
They **do not automatically execute commands or send a chat turn**. The agent
can inspect events during its next interaction and update the surface with
`canvas.show`. `canvas.close` preserves the last content. Layout controls such
as `canvasWidth`, `navPinned`, and `navExpanded` are in shared `view` state.

## Interactive conversation surfaces

Use `canvas.apps.create` for interactive UX that agents will refine during a
conversation. It gives one stable tab, shared typed state, agent/user event
parity, retained design revisions and reviewed host actions. See
[the contract](shell/conversation-surfaces.md). `canvas.show` retains its
immutable saved definitions; document revisions share one artifact tab.

## Artifact library and tabs

`canvas.show` saves a definition with a stable ID, title, format, owning chat,
workspace and creating user message. Direct content opens a new artifact; a
canonical file path within the same chat/workspace reuses its existing tab.
Unchanged file content does not add a revision; changed content saves a new
immutable version. File changes are not watched. Different files with the same
title stay separate. Existing artifact IDs and saved content remain valid;
older duplicate IDs are retained rather than guessed together.

Agents refine a document through `canvas.versions.inspect {id}` and
`canvas.versions.revise {id, expectedRevision, content, title?}`. This changes the
saved Canvas definition, **not the source file**. A2UI revisions supply `surface` instead of `content`. Stale revisions and dirty
viewer edits are rejected. `canvas.versions.restore {id, version,
expectedRevision}` copies a saved definition into a new latest version; it never
rewrites earlier definitions or replays work. Interactive surfaces use the
existing `canvas.apps.revise/restore` contract and its shared-state revision.

The header offers **Latest** and numbered saved versions. `canvas.select {id}`
follows Latest; `canvas.select {id, version}` selects an exact read-only version
for that client, even as another client edits Latest. Historical chat artifact
buttons record the version published during their turn. Saved surface versions
include the inputs captured when that definition was created; older versions
without recorded inputs are labelled explicitly. Live interaction is available
only on Latest. Retained definitions are not evicted after twenty revisions.

The document action row shows the open saved version and whether a newer saved
version is available, even with Details closed. **Refresh** checks the existing
saved-state owner and version list. Latest follows new passive document definitions;
an exact version stays selected. Refresh is not a page/iframe reload, does not
rerun document code, and does not import file edits or create a revision.
Unfinished viewer input and the composer draft remain in place. A failed check
retains the open document and shows an error beside Refresh; try Refresh again
after the connection recovers. Use the existing version button to choose another
saved version. Live apps and websites keep their separate lifecycle controls.

For an already-open executable **HTML or Babylon** preview, Latest discovers new
saved versions but retains the exact running definition and its unfinished input.
The row distinguishes **Viewing v1 · v2 available · preview retained**. Only the
explicit **Load v2 — resets preview and runs its code** control replaces that
preview with the named saved definition. Changing executable versions through
Details also offers **Keep current preview** before an explicit reset. Declared
dirty edits still refuse replacement; agents cannot reset previews. Copy,
Download, Source and renderer commands remain bound to the running version.
Missed updates, Refresh and transport reconnect do not adopt a new executable
definition. Newly opened previews behave normally; this does not serialize DOM
or JavaScript state across a new browser page or a cold restart.

An accepted **Load** and its following state read are separate outcomes. If Load
is accepted but that read fails, the controls retain its exact receipt and say
the named version was accepted but the displayed version needs confirmation.
They do not claim the old preview was retained. A lost or unreadable mutation
acknowledgement (including proxy HTML or malformed JSON) remains unconfirmed;
a confirmed refusal remains a refusal. Use **Refresh** to read saved
state before another Load; neither failure automatically retries the reset.

This executable-preview exception was approved on October 28, 2026: “Preserve
interactive previews; load new versions explicitly.” Passive documents retain
automatic Latest following; saved histories and exact historical selections are
unchanged.

For MCP Apps, reopening the same completed operation reuses its tab. A tool may
supply `_meta["amplifier/presentationId"]` in its result to identify one durable
presentation across later calls. The ID must include the tool's run/input
identity; unrelated runs must use different IDs. Unified namespaces it by server
configuration, verified account identity and resource URI. Different launcher
methods can reuse that explicitly identified dashboard only while their saved
app grants, schemas and requested permissions remain identical. Existing saved
artifact IDs and version links are preserved when adopting this identity. Without
that explicit result metadata, different operations remain separate, even when
their titles or results match. Source HTML and the launch result are retained
locally per version; old views do not depend on the rolling operation list.
Historical versions cannot call tools, read server resources or reconnect.
External services and non-self-contained scripts are still not made durable by
this identity contract; a live dashboard may need its original server.

`canvasArtifacts` contains metadata and `$resource` body references. Only the
active `canvas` body is included in ordinary browser state, except HTML over 1 MB,
which carries a `contentResource` reference instead; the agent overview
contains a bounded current-chat index. `get_state` can page a saved body, for
example `/canvasArtifacts/0/body/content`. `canvas.select {id}` reopens the latest saved
item, `canvas.tabClose {id}` closes only its tab, and `canvas.reopen` opens the
panel. The current shell uses `canvas.visibility {open, sessionId, canvasId}`
on an attached client to hide/show retained viewers immediately. This keeps edits
and iframe state while hidden, without changing artifact identity or scanning
workspaces. `clientId` can explicitly target an attached client for agent use.
The legacy close/reopen lifecycle remains available. Selection and file reads are scoped to the chat and workspace. Agent
publications default to the calling session even when the user views another
chat. Background publications do not replace the user's active preview.

For an agent, `canvas.select {id, clientId?}` validates the artifact against the
calling chat and routes to a client already displaying that chat. A matching
bound client is preferred; otherwise one matching client is selected. Multiple
matching clients require an explicit `clientId`. If no client displays the chat,
selection returns `canvas_client_required` with instructions to open it first.
It never navigates an unrelated client, and unsaved viewer edits still block
replacement through the ordinary Canvas guard.

Agent state reads and selection receipts use the same caller scope.
`canvasContext` reports the chosen client, matching client IDs, and whether the
presentation is attached, ambiguous, or unattached. `get_state {clientId, ...}`
can address one matching client explicitly. Without a unique target, `/canvas`
is a closed placeholder and the caller's saved artifact index remains available;
the host does not present another chat's Canvas or composer as the caller's.
If the client navigates after selection commits, the accepted receipt remains
successful and its readback reports a detached placeholder instead of failing
the completed action. Explicit reads still reject a client displaying another chat.

Chat receipts reopen exact definitions from their publishing turn. Forks inherit snapshots
only through the retained user messages. Editing forks before the original user
message, so artifacts from that message and later turns remain in the original
conversation. Fork/edit boundaries also support mixed voice and typed chat. Spoken exchanges that never required a manager delegation are retained as explicitly labelled historical context; they do not replay work. Native transcript timestamps keep later speech and tool results out of earlier branches. Legacy transcripts without a reliable boundary can still be forked in full. Tab changes do not preserve running JavaScript memory inside an
HTML preview; the authored document is the durable snapshot.

On first upgrade, the previous active preview is retained. Accepted inline
`canvas.show` tool calls in existing checkpoints are recovered without replaying
tools or executing scripts. Missing historical file content is not fabricated by
rereading the current file. Recovered items appear in the saved library with
closed tabs. Recovery is idempotent.

## Browser preview

Publish `{"kind":"browser","title":"My app","url":"http://localhost:3000"}`.
Only HTTP(S) URLs without embedded credentials are accepted. The preview uses
an opaque-origin iframe with scripts and forms enabled. The parent app remains
inaccessible. Unlike authored HTML previews, it can load external website assets;
it cannot report document contents or controls through `canvas.snapshot`.

Reload is a shared `canvas.view` control. `canvas.openExternal {id}` requests a
regular browser tab (browser popup permissions may apply). **Open in browser**
and **Preview help** stay visible even with collapsed canvas controls. Help is a
shared `canvas.view {id, patch: {help: true}}` control.

An HTTPS host detects and explains blocked HTTP embeds before loading them
(loopback addresses have browsers' secure-context exception). Prefer a reachable
HTTPS URL or open the HTTP URL in a separate tab. A loopback address names the
viewing device, not necessarily the machine running Amplifier; remote users
should use the service host's LAN or Tailnet address. The app warns about this.

Frame navigation reports are `unverified`: an iframe load event does not prove
embedding was allowed or that the app succeeded. Cross-origin browser security
prevents reliably distinguishing every network, authentication, or frame-policy
failure; an unresponsive or blank preview always retains the external fallback.
Some sites
block frames, and storage/cookie/API-dependent apps may need the external browser.
No embedding restrictions are bypassed or proxied. See the
[iframe sandbox reference](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/iframe).

A browser artifact saves its URL, not its server process, browser profile,
navigation history, login, or website content. A launched app must still be
running when reopened.

## Babylon.js 3D scenes

Publish `canvas.show` with `kind: "babylon"` and an HTML document in `content`
(or a workspace `path`). Babylon.js 9.26.0 is bundled locally and available as
`BABYLON` before authored scripts run. No CDN script tag or install is needed.
The agent receives this capability and authoring guidance on each turn.

Create a canvas, `BABYLON.Engine`, `Scene`, camera and lights, then call
`engine.runRenderLoop(() => scene.render())` and resize the engine when the
window changes size. `BABYLON.Engine.IsSupported` is a boolean getter for a
WebGL availability check. Use procedural meshes or embedded assets; the isolated
preview cannot fetch external resources, load CDN decoders or use WebGPU.

The scene follows normal artifact persistence and tab behavior. Download exports
a standalone HTML file with Babylon included; Copy copies the authored source.
The library is not stored in chat history or sent to the model. Reopening starts
the authored scene again; JavaScript memory and camera positions are transient.

Use labeled HTML controls for agent interaction through `canvas.interact`.
The agent can inspect document text, controls and runtime errors, but does not
automatically perceive the rendered 3D pixels or scene graph. GPU/WebGL support
is required. Unity build tooling and specialized Unity hosting are not included.

### Exact saved links in assistant messages

Publication and inspection receipts return `reference`, for example:
`amplifier-canvas://artifact/ARTIFACT_ID?session=SESSION_ID&version=2`.
Use it in an ordinary Markdown link: `[Review version 2](REFERENCE)`.
The chat renderer recognizes only this exact format. Clicking reveals that
saved definition in the clicking client, preserves the composer, and never
submits a message, changes chats, fetches an external URL, or runs a tool.
The shared `canvas.select` action validates session ownership and revision;
missing versions and a changed conversation produce an explanation. Section
anchors and editing selected text are outside this saved-reference contract.
Do not replace a reference with a localhost/server URL unless the user wants
an external browser. Publication, accepted selection and browser mount remain
separate pieces of evidence.

### Inline previews

#### Review material in questions

Use readable Markdown for question prompts, descriptions and answers. Attach up
to four exact same-chat saved versions using
`question.create {sessionId,prompt,required,dependency,artifacts:[{id,version}]}`.
`question.supersede` accepts the same list for a new pending question. IDs and
versions come from saved Canvas receipts, not arbitrary URLs or file paths.
The host retains bounded title/kind/owner and content-addressed body identity
without copying document bodies into the question. An answer retains this
reference set and the question revision; opening a preview does not attest that
the person read it, answer the question, grant permission or start work.

Compact review cards expose **Preview here** and **Open in Canvas** separately.
The shared presentation owner also handles question Preview/Open/Hide. A closed
Canvas retains its renderer, so opening that version at a question is a transfer,
not merely closing a panel. Stateful HTML/3D requires the same explicit Keep/Reset
choice; reset never bypasses dirty edits. Bodies load only after explicit
expansion. Supported HTML mockups use the same
sandbox as Canvas, never parent-document HTML. Unsupported kinds and unavailable
versions report their limits instead of substituting Latest. Saved question and
answer source is preserved, including whitespace. Free-text drafts are kept in
the originating browser tab's session storage, keyed by host/chat/question ID
and `contentRevision`; storage failure is visible. They are not sent until
explicitly answered.

An open question review remains mounted across status and delivery updates until
the person closes it. Answer, cancel and supersede reject before changing the
record if any client has declared that question's preview dirty. The editor and
answer draft remain reachable; finishing/cancelling the edit permits a new
explicit attempt. These guards never discard the edit or replay the answer.

An inline view binds either a message publication or
`questionId` plus `questionContentRevision` from the question's immutable
`contentRevision`, never both. This is not the record's status `revision`, which
increments when answered or closed. The question binding is checked
again on resource reads. Exact historical references remain inspectable after
answering; new decisions use a new/superseding question rather than changing a
past answer.

Saved document and image publications also appear beside their originating chat
message. The inline preview binds the publication's exact saved version; opening
it in full view selects that same artifact/version in Canvas. A later revision
does not silently replace a displayed earlier version. Tool apps, interactive
surfaces and external websites retain their existing Canvas link until their
inline lifecycle is qualified.

CanvasViews owns both presentations. `canvas.views.inline` requires the artifact,
version, a message publication or exact question source, and a client-local
preview mount identity. It validates the source and selected conversation before
allocating a view. Bundled cards use `presentationRequest` to require the matching
accepted presentation; a superseded choice cannot recreate an old preview. A repeated mount
request reuses its view; a new mount receives a distinct view so a late unmount
cannot close its replacement. All reads and commands retain the existing
resource/revision/generation checks. `canvas.views.release` closes only that
inline view and refuses unsaved edits. Conversation transitions protect dirty
views and clear clean inline leases; a new browser page never inherits leases
from its predecessor. Saved content is unaffected by preview disposal.

The app's **Reload now** control also protects unfinished Canvas input. Mounted
native renderers and interactive apps register local dirty intent before sending
the canonical declaration, so a pending or rejected acknowledgement still blocks
reload. Only the latest successful clean declaration clears that local guard;
unmount retires it. Canonical dirty views provide an additional safeguard. Reload
checks again after draft/checkpoint preparation, immediately before navigation.
This does not turn a best-effort observation checkpoint into saved input, and does
not intercept the browser's own reload/close controls.

Preview bodies load only near the viewport. A client may retain up to eight
inline previews; additional artifacts remain available in full Canvas, and the
user can close a preview to free capacity. This bounds mounted frames without
silently discarding interactive input. Multiple images in a message share a
gallery; only the selected image body is fetched, and revisited images can show
a thumbnail. Loading labels describe resource loading, not model generation or
an invented completion percentage. Appearance tokens govern frames and controls;
these saved-preview placeholders do not animate.

Image downloads return the bytes of the exact saved version, with the matching
image MIME type and extension. `canvas.views.imageDraft` copies those bytes into
the requesting client's unsent attachment list, preserving draft text. It never
runs generation, submits a message, or overwrites the source image. Inline and
full-view image controls use these same owner actions.

### PDF documents

Open a workspace `.pdf` with `canvas.show` (`kind: auto` or `pdf`, `path`) or
a file link in chat. Canvas saves an immutable original-byte snapshot (20 MB
maximum) and loads it separately from regular application state. PDF.js and its
worker, fonts, character maps, and codecs are bundled locally. The viewer offers
page navigation, zoom, fit width, text selection where present, and original-file
download. Reopening a saved artifact does not rerun a tool or reread the source
file. Password-protected or damaged PDFs show a download fallback. PDF editing,
annotations, embedded scripts, and XFA forms are not supported.
