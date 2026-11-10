# Appearances: shell, components and artifact viewers

Start with the [Appearances vision and ownership contract](appearances.md).
Themes, modular shell customization and interaction presentation are parts of
that user-facing umbrella.

Agents and extension authors can start with the
[repository-owned shell skill](../../skills/amplifier-shell/SKILL.md).
See [behavior installation and app defaults](behavior.md) for discovery.
See the [customization status and proposed next milestones](customization-roadmap.md)
for the boundary between today's extension contracts and the remaining shell work.
For complete light/dark palettes, backgrounds and the decoration preference,
see [Themes](themes.md).
See [Component contributions](components.md) for registered header, status,
composer-action, canvas-toolbar and Settings slots.

The workspace manager and conversation list are independent registered components.
Additional instances can follow the active workspace, pin a workspace, or show all chats.
An external module can replace either component while the app stays open.

The renderer registry supports validated hot-loaded artifact viewers. Canvas
has one primary viewer per client; inline message/question previews have separate
bindings. See [Artifact viewers](#artifact-viewers) for the current API,
ownership boundaries and checks. Early milestone identities and measurements
are retained in the [historical evidence](evidence/navigation-content-workspace.md).

The default shell retains workspace creation, folder browsing, chat search,
pagination, pinning, renaming, removal, unread indicators and history refresh.
Its declarative `hideWhen` binding hides the workspace manager when the default
chat list shows All chats, without discarding the manager's view state.

## Ownership and boundaries

| Contract | Owner | Evidence |
| --- | --- | --- |
| Canonical chats, workspace registrations, runtime ownership | Existing AppService and Amplifier Foundation | Existing navigation, history and handoff tests |
| Package manifests, immutable digests, host validation receipts | `amplifier_web/shell_modules.py` | Digest, stale-host, compatibility and capability tests |
| Client compositions, revisions, prepared changes, instance UI state | Shell records in the existing app database | Isolation, persistence, compare-and-set, idempotency and revert tests |
| Bounded navigation queries | Adapters over the existing workspace/chat projections | Existing 206-chat browser fixture; no additional conversation store |
| Loading, subscriptions, component errors and browser status | `components/web-client/src/shell/runtime.jsx` | Production browser proof and validation harness |
| Workspace and chat controls | `components/web-client/src/shell/navigation-components.jsx` | Existing component tests run against public host fixtures |
| Public authoring API | `packages/shell-sdk` | External navigator in `examples/shell-navigator` |
| Theme tokens and layout presentation | Host root and existing panel layout | Live preview, CSS reordering, revert and stable iframe checks |
| Artifact card, disclosure, viewer and saved-output controls | [Artifact controls and reuse](../canvas.md#artifact-controls-and-reuse) | Exact-version presentation, control parity, dirty-state and narrow-layout checks |

The host has one event stream. The bundled browser receives requested shell
resources through `state.shellResources` on that stream after its initial
descriptor/resource bootstrap; see the transport contract below.
Queries expose summaries, the selected workspace registration, folder rows and
navigation state. They do not expose transcripts, composer drafts, credentials
or runtime mount plans through the SDK. The existing app-wide observation API
continues to exist outside the module SDK.

The built-in browser explicitly requests its required navigation resources
before construction; the snapshot names its supplied resources and omitted pages
are not empty results. See [the transport contract](../browser-state-transport.md#shell-bootstrap-and-active-resources).
Custom navigation packages and explicit `shell.query`/`shell.inspect` reads retain
the complete bounded navigation snapshot. The bundled HTTP endpoint requires
resource demand; client and server ship together without old-bundle fallbacks.
Wire compaction alone does not enable omission. Component instance generation and command authority are
unchanged.

Shell compositions and view state use the existing client-attachment contract.
Each page attaches with a new client ID; reload or duplication resumes a copy
of the previous client's presentation, drafts and shell preferences, so two
live pages do not share writable presentation state. A fresh tab starts with
defaults. Conversation selection is client-local, while canonical sessions,
artifacts and tool work remain host-owned. Durable cross-device preference
synchronization remains later work.

Legacy navigation preferences are copied once when a client is first seen.
Agents should use `shell.query` and `shell.view.update` for the actual module
state. Legacy `/chatNavigation`, `/workspaceExplorer` and `view.update` navigation
fields remain app defaults. The agent state overview explains this distinction.

## Package and component contract

A native package is one compiled ESM artifact, at most 250,000 characters, plus
a manifest. The manifest names a semantic version, `apiVersion: "1.0"`,
`profile: "trusted-native-navigation-v1"`, a `stateSchema`, and capabilities.
The artifact exports a default factory `({React}) => Component`; the component
receives `{host}`. React comes from the host. The dependency-free public SDK
provides `defineModule` and `useNavigation`.

`host` exposes the instance/client IDs, `getSnapshot`, `subscribe`, `dispatch`
and `setDirty`. Subscriptions must return cleanup functions. Components use
`dispatch('view.update', {patch})` for their own navigation state. Other
supported actions pass through `shell.command`, which checks the declared
capability before invoking the existing shared action handler. The namespace
does not provide runtime/provider/bundle configuration operations.

Navigation snapshots include `attention.items` containing only error review
receipts (`id`, `sessionId`, `fingerprint`, `read`) for the bounded chat rows.
With `chats.manage`, pass the observed IDs and fingerprints to `attention.read`
through `host.dispatch`. This reviews the exact failed-turn notice, clears its
historical navigation marker, and preserves the saved error and conversation.
The module cannot review unrelated inbox items or omit fingerprints. Later
errors become unread again; pending approvals, required questions, uncertain
answer delivery and blocked work keep their attention markers.

Supported capabilities are navigation reads and explicit selection, workspace
management, chat management, folder location reads and history refresh.
Changing a filter or browsing a folder never selects a conversation. Explicit
selection continues to use the existing selection operation.

Each composition holds up to 12 instances in the navigation slot, with stable
IDs. CSS order changes placement without moving or recreating mounted nodes.
Presentation supports light/dark/system appearance, accent color, density and
the existing balanced/conversation/work layouts. Custom CSS skins continue to
use the existing theme service. [Themes](themes.md) and
[component contributions](components.md) describe the supported styling and
additional slots; [Appearances](appearances.md#shipped-building-blocks-and-remaining-work)
separates those from future work. Artifact renderers have the separate contract below.

## Stage, validate, prepare, activate

Agents and people use the same `/api/actions` command interface:

1. `shell.packages.stage {manifest, source}` returns a digest; it executes nothing.
2. `shell.packages.validate {digest}` runs the host validator. Its receipt binds
   the manifest and compiled bytes to the API/profile and current host runtime.
3. `shell.inspect {clientId}` returns the current revision, composition, package
   registry and browser activation evidence. Find the client ID in the observed
   device or `window.amplifier.shellClientId`.
4. `shell.changes.prepare {clientId, expectedRevision, composition}` returns a
   reviewable change with its previous and proposed composition.
5. `shell.changes.preview` or `shell.changes.apply` takes the client ID, change
   ID and expected composition revision. Preview does not replace the committed
   composition. Both may initially report `awaiting-browser`.
6. Inspect again for the target revision's browser report. `ready` means its
   components mounted. It is separate from package validation and does not
   prove complete visual or functional acceptance.
7. `shell.changes.revert` restores that change's previous composition at the
   supplied current revision. `shell.query {clientId, instanceId}` reads a
   module's current bounded projection.

Use an explicit request `id` to retry a mutation after an uncertain response.
Reusing an ID with different arguments is rejected. Composition revisions are
separate from chat streaming revisions. Stale preparations cannot overwrite
newer compositions. Committed module bytes are rechecked before activation and
serving. Rebuilding/upgrading the host invalidates older validation receipts.

Removal or replacement defers while the instance has a navigation form or
declared dirty state. A replacement with a different `stateSchema` also defers;
the current instance remains active. Matching schemas preserve host-owned view
state; module authors must store durable edits there or mark them dirty. This
does not transfer arbitrary React local state or implement schema migrations.

## Validation and recovery

The validator requires Node and a toolchain containing Playwright/Chromium and
Rollup. A prepared source checkout uses `.build/web-client/node_modules`; a packaged installation
can set `AMPLIFIER_SHELL_NODE_MODULES` to an explicitly installed toolchain.
Missing tooling prevents approval rather than accepting an agent's test claim.

The host starts a sterile loopback fixture with the production React runtime,
checks the compiled artifact for unsupported imports/dynamic code generation,
and exercises empty/populated snapshots, mount/update/unmount/remount and host
subscription cleanup. Network access outside that fixture origin is blocked.
Validation has a 45-second limit and does not hold the app command lock.

This is a **trusted native code profile**, not an isolation boundary. Capabilities
constrain SDK dispatch, but same-origin JavaScript retains app authority. The
closed compiled import graph does not audit the provenance of every already
bundled source line. Validation is a compatibility check, not a security review,
and cannot prove all event-listener cleanup or arbitrary live-data behavior.
Untrusted modules need a future isolated execution profile.

Rendering failures are contained to the component and reported as incomplete
activation. The navigation footer offers last-working and default recovery.
Opening `/?shell=recovery` skips all optional modules and allows restoration of
the default composition. Native infinite loops cannot be caught by a React
error boundary; recovery requires opening that bypass URL. Explicit recovery
can discard module-local edits, but does not clear chat drafts or conversation
data. Last-working compositions are saved only after a successful browser
report; a broken live render cannot promote itself through package validation.

## Reproduce the proof

Use an owned test environment with synthetic state. These commands launch
fixture services; choose checks for the changed boundary rather than treating
all historical verification as a routine release requirement.

```sh
uv sync --frozen
python scripts/web_client_build.py prepare
npm ci --prefix .build/web-client
npm ci --prefix tests/web
npm exec --prefix tests/web playwright install chromium
python scripts/web_client_build.py build
npm run test:shell-browser --prefix tests/web
```

The maintained [shell browser scenario](../../tests/web/shell-browser.mjs)
exercises the production host and a separately authored navigation package.
[Shell module tests](../../tests/test_shell_modules.py) cover host-side package,
revision, capability and recovery behavior. Fixture validation, live browser
activation and installed-package acceptance are distinct evidence.

## Baseline and deferred work

The [historical baseline and deferred-work record](evidence/navigation-content-workspace.md#baseline-and-deferred-work)
preserves the initial scope and limits. For current supported customization and
remaining work, use [Appearances](appearances.md#shipped-building-blocks-and-remaining-work).

## Navigation milestone verification record

The [frozen navigation verification record](evidence/navigation-content-workspace.md#navigation-milestone-verification-record)
retains the original test counts and 0.10.8 comparison. Those measurements do
not describe the current application or establish a new performance result.

## Artifact viewers

`CanvasWorkspace` owns view containers and an Open with selector. A registry
routes every existing artifact format through its standard adapter: Markdown,
text, code, JSON/JSONL, images, HTML, Babylon, Mermaid, Graphviz, A2UI, websites
and MCP Apps. Existing format controls and sandbox restrictions remain in
those adapters. Contributed renderers enter the same host through a validated
manifest and compiled artifact, without a format-specific host branch.

The primary view follows the client's selected artifact; use `canvas.select`
to choose a saved artifact. Canvas mounts one viewer per client; there is no
secondary pinned pane or `canvas.views.open` /
`canvas.views.close` action. Legacy secondary preferences remain historical
client data and are neither mounted nor used to block the primary view.

Inline message and question previews are separate, exact-version renderer
leases, not secondary Canvas panes. They share the canonical artifact owner
and obey the [one-full-presentation contract](../canvas.md#one-full-presentation-per-saved-version).
Use the [inline preview contract](../canvas.md#inline-previews) for source
binding, supported kinds, dirty protection and release.

Artifacts retain their existing library IDs and immutable stored bodies.
Renderer choice, generation, dirty state, controls and reports belong to the
view within the existing client presentation record. No second artifact store
or conversation store is introduced. Closing a document retains its saved
artifact. Reload and host restart restore saved presentation records, not
arbitrary renderer-local React state.

MCP Apps use their existing tool adapter and authority checks. Contributed
renderers do not grant tool capabilities, add arbitrary Canvas pane counts or
provide automatic state-schema migration.

### Renderer package and public SDK

Use the same `shell.packages.stage` and `shell.packages.validate` sequence.
A renderer manifest adds a label and its supported resource kinds:

```json
{
  "id": "example.document-reader",
  "label": "Reading view",
  "version": "1.0.0",
  "apiVersion": "1.0",
  "profile": "trusted-native-renderer-v1",
  "stateSchema": "canvas-view-v1",
  "resourceKinds": ["markdown", "text"],
  "capabilities": ["canvas.resource.read", "canvas.view.update"]
}
```

The package exports `({React}) => Component` and receives `{host}`. Public
`useCanvas(React, host)` subscribes to a snapshot containing only `viewId`, the
artifact's identity/content metadata and that view's presentation state.
It does not supply chat history, drafts, credentials or tool capabilities.

| Host API | Behavior |
| --- | --- |
| `getSnapshot()`, `subscribe(listener)` | Observe the bound artifact and view; return subscription cleanup |
| `readSource()` | Explicitly fetch the bound immutable text, including large stored documents; stale bindings reject |
| `dispatch('view.update', {patch})` | Merge presentation state, requiring `canvas.view.update`; accumulated state is limited to 16 KB |
| `dispatch('view.report', {part, status, message})` | Report rendered content with `canvas.view.report` |
| `setDirty(boolean)` | Defer renderer replacement and navigation while edits are pending |

Matching state schemas preserve stored view state across renderer changes.
Arbitrary React local state is not migrated. Modules should prefix their custom
view keys and use the host state for preferences they want to keep. Large HTML
and 3D sources stay out of routine snapshots; `readSource()` is an explicit
on-demand read. Independent example `examples/shell-reader` imports only the
public SDK and preserves its text-size choice without editing source content.

This remains the **trusted native code profile** described above. SDK
capability checks are not an isolation boundary. Validation exercises empty
and populated fixtures for each declared resource kind, plus mount/update/
unmount/remount and subscription cleanup. Fixture success is separate from
live browser activation and cannot establish correctness for all live data.

### Agent actions and precise targets

Attach the intended client and use its normal action transport. HTTP requests
can carry `X-Amplifier-Client`; agents without an attached transport can pass the
observed `clientId` in any `canvas.views.*` action. A conflicting attached client
is rejected, and an unknown ID never creates a presentation implicitly.
`canvas.views.inspect {clientId}` returns current views and compatible,
validated renderer choices. Every view mutation requires all four values from that observation:

```json
{
  "viewId": "primary",
  "resourceId": "observed-artifact-id",
  "resourceRevision": "observed-content-digest",
  "generation": 3
}
```

| Action | Additional arguments and outcome |
| --- | --- |
| `canvas.views.renderer` | Target plus `{renderer}`; choose a built-in ID or validated package digest |
| `canvas.views.command` | Target plus `{action, args}`; explicitly address supported view/report, HTML interaction, A2UI or export controls |
| `canvas.views.dirty` | Target plus `{dirty}`; declare unfinished local edits |
| `canvas.views.recover` | Target; restore its standard viewer, retaining the artifact |
| `canvas.views.status` | Target plus `{status, message}`; browser evidence of loading, ready or error, separate from package validation |

The host rejects changed resource identities, revisions and generations.
Renderer replacements and reopened views invalidate late actions from previous
mounts. Existing action receipt IDs support retries without repeating an
accepted mutation. A missing package retains its saved choice and artifact,
shows the standard viewer and exposes recovery. A live render failure stays
within its view. `?shell=recovery` bypasses optional renderer imports as well as
optional navigation modules; explicit recovery can discard module-local edits.

Opening Chat overview hides the workspace with `hidden` and `inert` while
retaining the mounted viewer and its local state. The visible **Close Canvas**
button and header toggle use `canvas.visibility` to hide/show the retained panel;
they do not discard dirty edits or close document tabs. See
[Canvas layout](../canvas.md#workspace-layout) for those controls.

Operations that would replace a dirty viewer remain guarded. These include
`canvas.close`, closing the current document tab, and artifact, chat, workspace,
fork/edit or tool-app transitions that would replace that view. The host refuses
before changing the selection, composer target, binding or generation. These
parent operations return HTTP 409 with code `canvas_view_dirty`; existing
view-specific renderer replacements return their deferred result. Shared chat
deletion checks affected clients too, and MCP App loading rechecks after
asynchronous resource I/O. The browser navigation queue waits for preceding
dirty declarations so an immediate chat switch cannot overtake the edit report.
Finish or cancel the edit before retrying, or explicitly recover that view to
discard its local edit while retaining the saved artifact. Dirty state is a
navigation guard, not persistence of arbitrary React state across a page reload.

Source and iframe document requests also carry the view target. Each HTML
bridge receives only its own frame messages. Ordinary artifact bodies are
fetched on binding changes, not on each view preference, theme or chat-state
update. Renderer views use the existing event stream; this is not a
large-workload performance claim.

### Content-workspace acceptance

After the dependencies and production build described above:

```sh
npm run test:canvas-renderers-browser --prefix tests/web
npm run test:canvas-dirty-browser --prefix tests/web
npm run test:smart-tools-browser --prefix tests/web
uv run pytest tests/test_canvas_views.py -q
```

The maintained [renderer browser scenario](../../tests/web/canvas-renderers-browser.mjs)
checks a single Canvas viewer, independently built/staged/validated renderer
loading, immutable content, saved renderer preferences, composer retention,
reload, stale HTTP reads/actions and failed-render recovery. It also distinguishes
an optimistic Details change from an accepted receipt before reloading.

The [dirty-view scenario](../../tests/web/canvas-dirty-browser.mjs) uses an
editor whose unfinished text exists only in React state. It checks preservation
through Overview, panel hiding and Details layout changes; guarded tab/selection
transitions; a delayed dirty declaration racing navigation; and explicit recovery.
[Host-side tests](../../tests/test_canvas_views.py) cover legacy secondary-state
inertness, exact targets, renderer compatibility/capabilities and dirty guards.
The [MCP scenario](../../tests/web/smart-tools-browser.mjs) checks the real
local tool adapter, frame isolation and presentation changes without mutation replay.

These synthetic fixtures do not establish live-provider, voice or arbitrary
third-party renderer compatibility. Record a run's exact inputs and outcomes;
this guide's historical [content-workspace verification](evidence/navigation-content-workspace.md#content-workspace-acceptance)
preserves the 0.11.11 counts and earlier two-view proof without relabeling them
as current acceptance. Follow the [focused release policy](../validation/release-qualification.md)
when selecting installed-package and changed-flow checks.

## Conversation-owned interactive surfaces

Use [the surface contract](conversation-surfaces.md) for dynamic interfaces
that users and agents can operate together and refine in one tab.

For a file artifact, `canvas.copyPath {id, format: "absolute" | "relative"}`
copies its path on the app server. The same action is available through the
view command with an exact target; it never opens a local desktop application.
