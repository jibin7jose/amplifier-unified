# Historical navigation and content-workspace evidence

This is retained evidence from the early shell milestones, copied verbatim from
`docs/shell/README.md` at `e2dd0af6833302a0439c1e9f54ab368d5771ea3d`
(source SHA-256 `94769a221fb8f1b5c548872c1bc6b327d06b15a7916cfead894e2f240fd84e26`).
The ranges below preserve the original identities, counts, timing samples,
commands and stated limits. “This branch” and “this milestone” refer to those
historical records, not the current checkout or a new verification run.

Use the [current shell guide](../README.md) for supported behavior and maintained
checks. The secondary pinned viewer described here was deliberately removed by
[PR #211](https://github.com/microsoft/amplifier-unified/pull/211), commit
`cf535c3d7afd701392e0d28646baca70a3aecf2b` (0.20.21). Inline message/question
previews have their own current [presentation contract](../../canvas.md#one-full-presentation-per-saved-version).
Historical commands are preserved for provenance; they are not a request to run
old broad campaigns for a documentation change or a routine release.

## Milestone context

The first milestone implemented the navigation slice of the shell plan, based on
Unified `81f2182b6112e4162d982291151c489bdec6ef8a` (0.10.8). The workspace
manager and conversation list are independent registered components. Additional
instances can follow the active workspace, pin a workspace, or show all chats.
An external module can replace either component while the app stays open.

The content-workspace milestone, integrated with Unified 0.11.9, adds a
renderer registry, validated hot-loaded artifact viewers, and a second pinned
artifact view. See [Artifact viewers](#artifact-viewers) below for its API,
ownership boundaries and acceptance checks.

## Reproduce the proof

From the repository root:

```sh
uv sync --frozen
npm ci --prefix frontend
npm exec --prefix frontend playwright install chromium
npm run build --prefix frontend
npm run test:shell-browser --prefix frontend
```

The proof starts a disposable real service and production frontend, opens the
app, then builds the separately authored navigator. It performs stage/validation
and agent-driven preview/apply/reorder/replacement/revert without rebuilding the
host or restarting/reloading it. It checks independent instances/clients, draft
and session retention, the actual iframe node, and a synthetic running job.
It separately tests private imports, failed fixture validation and a live-only
render failure followed by the recovery URL. Real provider calls are not part
of this fixture.

The ordinary chat/workspace browser suites cover the default modules, including
pagination, folder creation, persistence, agent parity, touch controls and old
CSS skins. `tests/test_shell_modules.py` covers stale revisions, idempotency,
changed bytes, stale host receipts, state incompatibility and recovery.

## Baseline and deferred work

The initial focused navigation baseline passed 47 Python tests before edits.
The existing 206-root-chat fixture, bounded pages of 100, desktop and narrow
layouts remain the comparison workload. This proof does not establish
large-transcript performance, voice continuity or compatibility with every
third-party MCP view. Those need their own integration acceptance.

Concurrent work on settings acknowledgement delays, transcript performance,
MCP system-theme updates and update-cache behavior is outside this change.
Existing location-picker actions still use the app's shared listing broker;
this milestone does not provide independently concurrent filesystem pickers.
Package distribution, signing, unattended trust policy, sandboxing, retention
of unused packages/clients, rich layout editing and schema migration remain
separate work. The SDK is provisional until this first milestone is reviewed.

## Navigation milestone verification record

On this branch, the complete Python suite passed **833 tests, with 10 skips**;
all **142 frontend unit tests** passed. Production build, production shell
proof, chat-library browser tests and workspace-explorer browser tests passed.
The shell proof additionally checks a left-side canvas resize, unsaved module
form deferral, and incompatible state replacement deferral. Browser screenshots
and raw timing samples are written under `output/shell-proof/` locally.

A three-sample production comparison used the unchanged baseline checkout and
this checkout on the same machine, with disposable 206-root-chat histories.
These are local observations, not a benchmark or release performance claim.

| Observation | Baseline 0.10.8 | Modular shell |
| --- | ---: | ---: |
| Median startup to 100 chat rows | 98 ms | 135 ms |
| Median search to two matching rows | 28 ms | 16 ms |
| Event streams per page | 1 | 1 |
| DOM nodes after search | 377 | 386 |
| Additional shell query after search | 0 | 44,828 bytes |

The app's legacy navigation projection remains in its regular state response.
Searching inside a module no longer filters that legacy projection, so the
measured app state was approximately 285 KB versus 252 KB after the baseline
search. Removing this duplication belongs with client-aware browser projection
integration; this milestone does not claim to resolve the existing large-state
performance work. Reproduce these samples with
`node frontend/tests/shell-performance.mjs /path/to/baseline-checkout`.

The navigation milestone was subsequently integrated with per-page attachment
and client-aware selection. The historical measurements above are not a
performance claim for the combined application or the content workspace.

## Artifact viewers

The primary view follows the client's selected artifact. **Open a second
view** pins the current ordinary artifact below it; that view remains bound
when the primary artifact or selected conversation changes. It does not select
the pinned artifact's conversation or change the composer target. Both views
can render the same artifact with different settings and renderers.

Artifacts retain their existing library IDs and immutable stored bodies.
Renderer choice, generation, dirty state, controls and reports belong to the
view within the existing client presentation record. No second artifact store
or conversation store is introduced. Closing a pinned view retains its
preferences and artifact. Reload clones these preferences through normal
client attachment; restarting the host restores the saved records.

MCP Apps retain their existing single active tool binding per client. They can
occupy the primary view alongside an ordinary pinned artifact. Pinning a second
MCP App is rejected. This milestone does not provide arbitrary pane counts,
drag-and-drop layouts, multiple simultaneous tool bindings, conversation
renderer replacement, or automatic state-schema migrations.

### Content-workspace acceptance

After the dependencies and production build described above:

```sh
npm run test:canvas-renderers-browser --prefix frontend
npm run test:canvas-dirty-browser --prefix frontend
npm run test:smart-tools-browser --prefix frontend
uv run pytest tests/test_canvas_views.py -q
```

The renderer proof opens a disposable production app before independently
building/staging/validating the example. It checks two instances with separate
settings, immutable content, an unchanged HTML iframe and its unsaved input,
preserved composer draft and conversation target, renderer changes, appearance
changes, reload, stale HTTP reads/actions and failed-render recovery. Desktop
and narrow-layout screenshots and structured evidence are written under
`output/canvas-proof/`.

The dirty-view proof validates and loads a minimal editor whose text exists only
in React state. It verifies Library hide/show retains the same input element,
panel-close refusal, a pinned dirty edit surviving primary/chat navigation,
primary transitions leaving drafts and binding generations unchanged, a chat
switch racing a delayed dirty declaration, and explicit targeted recovery
followed by close/reopen.

The MCP proof uses an independently authored official-SDK counter app and a
real local MCP tool server. A delayed accepted tool action finishes once while
appearance/layout/focus change, the iframe survives, and its pinned ordinary
artifact remains bound. It also covers sandbox isolation, stale server
configuration denial and reopening without replaying mutations. These fixtures
make no real provider/model calls; they do not prove voice continuity or every
third-party renderer's behavior. This branch does not restart a user preview,
publish a release or change the application version.

Verification with main at 0.11.11 (`b804878`) and the dirty-view repair:
**1,104 Python tests passed, 11 skipped**, and **154 frontend unit tests passed**.
The production build and ten browser suites passed: dirty-view navigation,
renderer hot loading, MCP Apps, navigation shell, canvas, saved artifacts,
panel layout, actual host restart, empty host and live clients. The final
navigation-queue refinement additionally reruns dirty-view, shell, live-client
and review-during-send coverage. The source distribution and wheel built and
passed release verification. The renderer proof also exercises recovery
startup, including accurate fallback/ready status.

The independently consumable saved-body restore fix (PR #76) restores ordinary
content and surfaces from compact client records after a host restart. This
branch applies the same behavior to its pinned-view resource endpoint. Large
HTML/Babylon bodies remain indirect. The restart proof is
`node frontend/tests/canvas-restart-browser.mjs`; run it from the repository root.

The separate composer preservation fix (PR #79) is also included: an edit or
send in a different chat first queues the previous staged draft with its original
session and payload. It does not delay navigation behind general requests.
The controlled debounce regression checks both host-saved drafts, sends once to
the intended chat, and confirms a canceled timer cannot restore a sent draft.
