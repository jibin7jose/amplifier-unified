# Workspace and chat navigation

The header keeps the chat title visible at every width. At 760 px and below,
chat uses the full screen width; the navigation icon opens a full-screen modal
with Close, Escape, and trapped keyboard focus. Navigation fills the viewport,
including narrow folded-phone displays, and respects display safe areas.
Selecting a chat or New chat returns to the conversation. Desktop pin/width
preferences survive resizing.
While New chat is awaiting its host receipt, configuration controls and text
entry are unavailable. The current chat and its saved draft remain owned by that
chat; the new draft becomes editable only after navigation settles. A failed
request restores the previous controls without applying edits to another chat.
Workspace browsing stays in the drawer. Tap **…** for a details sheet with the
full title, canonical session ID, path, and actions.

Workspace pages and Search chats have no remembered-chat return control or
working-chat banner. Open a named conversation from Pinned, Recent, search or a
workspace list, including the conversation already selected before browsing.
Escape clears a search query; with an empty query it stays in search.
Workspace file previews name their destination chat before opening. A guarded
`canvas.openFile {sessionId,workspace,path,showConversation:true}` opens the file
and shows that chat in one action, only from its workspace Files page. Failed or
stale requests do not navigate, and delayed receipts never navigate again.

Browsing still retains the selected chat's draft, attachments, reading position
and mounted Canvas editors. It does not stop work or revoke screen-sharing
consent. Call controls and Stop sharing remain visible; to stop non-voice work,
open that chat and use its normal Stop control. No selection or storage migration
is involved. The shared `view.update {workSurface:"chat"}` presentation action
remains available to authorized callers; it shows the selected conversation,
not a separate saved return target.

### Explicit Back/Forward history

Header Back and Forward traverse this tab's last 30 visited destinations:
Home, saved chats, workspace pages, All workspaces and Search chats. Mouse/pen
holding an arrow for 450 ms opens that direction's chronological steps, nearest
first, without also navigating. Repeated visits keep their individual keys.
Arrow Down or the context menu opens the same steps; touch retains ordinary
tap behavior rather than long-press navigation. Menus support arrows/Home/End
and Escape with focus returning to their opener.

The familiar history icon opens **Recent places**: at most 30 distinct
destinations ordered by latest confirmed visit, including Back/Forward traversal.
Identity is destination kind plus stable ID, not its title; same-name chats stay
separate. A workspace has one recent entry with its latest visited tab. Selecting
a recent place performs normal guarded navigation, not a jump to an arbitrary
old visit. Renames refresh known labels without changing recency. An older saved
trail seeds the list from its latest recorded occurrences; it has no timestamps
from which to reconstruct earlier traversal order.

The Amplifier brand and icon-only header Home use the existing Home action.
Home is site-level navigation beside the arrows, including the top row on narrow
screens, not a breadcrumb ancestor. Workspace/chat context stays in the breadcrumb.
Browse/Status shares a selection track without changing sidebar organization
semantics or the New chat control. This is explicit navigation, not restoration of the removed remembered-
chat banner or Escape-to-chat behavior; native browser Back/Forward is unchanged.

Entries contain only typed IDs, bounded labels, workspace tabs and visit keys.
Changing a workspace tab updates its current visit. Home view modes, search text, filters,
pagination and file-folder browsing do not create visits. Selecting the same
destination does not add an adjacent duplicate. New navigation after Back
replaces Forward; traversal itself moves the cursor without appending.

Unsent New chat is off-trail: Back goes to the current saved visit, while Forward
can still open its next visit. Neither direction recreates a draft. A newly
submitted chat becomes a normal saved destination. Drafts, attachments, Canvas
editors and settings guards keep their existing ownership; file preview still
uses its single guarded action rather than replaying a file-open command.

The cursor changes only when an accepted action receipt and authoritative state
agree on the host, revision and destination. Reconnect snapshots never append
visits, but can confirm an explicit traversal. Rejected, cancelled, missing or
unconfirmed navigation keeps the trail; errors remain visible and nothing is
automatically retried or replaced with a fallback. An unavailable entry can be
retried explicitly; absence from a bounded catalog page is not deletion.

The trail and bounded recent-place projection live in origin/tab-scoped `sessionStorage`, not the host database or
agent state. Reload retains it; a duplicate tab gets an independent copy.
Restoration requires an opaque scope derived from the authenticated sign-in,
process owner and host data identity. A new sign-in/root/secret starts a new
trail. A 401 clears the current trail and rejects late old-scope frames.
An already-open stream cannot detect a removed/expired cookie until an
authenticated request or reconnect observes it; the app has no logout endpoint.
This is presentation isolation, not protection against other same-origin script
or physical access to the browser. If storage fails, the navigation menus explain
that retention lasts only while this page stays open.

Focused checks (run against the candidate in an owned isolated environment):
`tests/test_auth.py`, `tests/test_history_scope.py`,
`frontend/tests/destination-history.test.mjs`,
`frontend/tests/destination-history-ui.test.mjs`, and
`frontend/tests/destination-history-browser.mjs`. The browser script requires an
owned `AMPLIFIER_TEST_OUTPUT` directory and rebuilt production assets. These
instructions are not evidence that the checks have passed.

The container-header **Canvas** button stays reachable on phones, where Canvas
fills the content area. **Hide canvas** returns to the chat while retaining its
scroll, draft and selected artifact, as described in [Canvas](canvas.md).
On larger screens it docks when the chat and panel have usable widths.
Visibility paints immediately through `canvas.visibility {open,sessionId,canvasId}`;
it has its own ordered queue and no busy spinner. The host persists only client
presentation, using cached catalog projections when available. It does not scan
workspaces or rewrite chat history and artifact catalogs. Cold source recovery
can still load inside the already-open panel.

Hiding retains the current viewer slots, including local edits and iframe state.
It does not initialize newly selected hidden artifacts. Dirty editors still block
replacement, tab closure, or navigation that would discard their input, including
while hidden. Legacy `canvas.close`/`canvas.reopen` retain their previous lifecycle
semantics for older clients. Agents use the same visibility action, optionally
with an explicit attached `clientId`; session and artifact bindings reject stale
targets. Hidden content is excluded from visible-control observations.

Below 1025 px, **More app options** includes chat details/export, Activity, and
Settings, alongside appearance, agent view, and feedback. On phones it is a modal
sheet. Escape, Close, and outside clicks dismiss it and return focus. Attention
appears on More and the relevant destination. Agents can control the disclosure
with `view.update {patch:{toolbarMenuOpen:true}}`, or open a destination via `panel`.

Default UI typography uses 14 px normal text, 12–13 px supporting labels, and
16 px larger text. Mobile editable fields are at least 16 px. The tokens
`--a-text-xs`, `--a-text-sm`, `--a-text-base`, and `--a-text-lg` let skins adjust this
scale without zooming layouts or icons. Only exact unmodified historical default
skins upgrade automatically; edited or renamed skins retain their saved CSS.

The default sidebar has independently collapsible **Pinned**, **Workspaces**, and
**Recent** sections. **Pinned** is hidden when there are no eligible pinned items;
its saved collapse preference is retained. One ordered list holds pinned chats
and workspaces together, and there is one workspace
explorer. Pins keep the same title, workspace subtitle, activity time, and actions
while browsing folders or filtering chats. Quiet Recent initially shows **20
eligible chats**, pinned or unpinned, not messages. Downward user scrolling within 120 px
of the list end adds 20 summaries at a time, including beyond 100, until actual
exhaustion. **Load older chats** is a quiet keyboard/screen-reader fallback
while older rows remain, including a tall viewport with no overflow. At
exhaustion the last chat ends the list: no count or All/View/Review chats footer.
**Search chats** still opens the full browser. Pins have their own bounded
pages. Pinning adds a shortcut without removing the item from Recent, workspace
lists or search. Each list contains an item once; the same item may appear in
Pinned and another list. Recent keeps its chosen sort independently of pin order.

Workspace row selection marks the workspace page currently being browsed. Opening
a chat clears that workspace highlight, including its containing workspace; the
chat's workspace context and the remembered browse location remain unchanged.

The initial 20–100 limit and origin toggle persist per client and mounted module.
Expansion beyond 100 is scoped to the attached client/module session, not copied
to a reload or another client. Reload uses the saved bounded initial preference.
Eligibility (including the current-root exception) is applied before slicing;
total and remaining use that same eligible population. Toggling origin retains
the loaded limit. Shrinking the population retains the saved limit while
deterministically reducing rows/counts. Requested limits remain multiples of 20:
requesting 120 with only 101 eligible chats returns 101 rows, limit 120 and zero
remaining. There is no mount, resize, SSE, section-open or recursive fill.
Hidden, inert and collapsed Recent do not load. Programmatic scroll restoration
and append anchoring cannot trigger another page. Paging reads summaries only,
not transcripts or offscreen title previews; it never creates a task.
Ordinary control/read errors keep the previous rows and offer **Retry**.
Retry performs the read-only `shell.query` action; it never automatically replays
an unknown presentation write or any task/input action. A host-owned module
view revision and query scope reject stale responses; controls and a surviving
scroll anchor are retained while rows change. Progress retains the existing
stable navigation recency until the ready/attention boundary.

The full All chats browser retains its existing filters and `navChatPage`;
legacy Recent retains independent filters and 50-row/40-row paging. Quiet Recent
does not share either counter, search, or sort setting. It uses activity ordering
across available workspaces and managed chats.

Workspaces uses the recent-workspace index and existing folder browser, with an
eight-workspace preview and **More workspaces** in the recent index. Available
registered folders appear even before their first chat; worker sessions never
become top-level chats. Selecting a
workspace opens its full chat list inside that section; **All workspaces** returns
to the index. Recent remains independently available. Search is visible; archive,
sort, location, and activity controls are under **Filters**. Collapse, filters,
and pages persist per client. The former simple view and Workspaces/All chats
switch are replaced by these shared sections.
Browsing folders, searching, filtering, and opening details never mount a runtime,
send a message, change recency, or acknowledge an unread response.

Recent hides agent-created independent roots by default. **Show agent-created**
reveals them for this client's mounted chat-list instance; reload retains that
instance's choice. Only the host-retained `collaboration.creatorSessionId` and
`requestId` together establish this origin. Navigation exposes an `agentCreated`
boolean, never the creation brief or grant. Missing/legacy evidence and human
fork lineage remain visible; workers and internal sessions retain their existing
classification and exclusion. All chats, full history and direct access remain
available regardless of this toggle. Pins stay in Pinned without duplicates.
The currently open commissioned root is eligible but receives no forced insertion
or displacement in a recency slice. Browsing never wakes or changes its task.
For example, an eligible current commissioned root ranked 25 remains open but
absent from Recent 20; it appears after downward loading or the fallback to 40.

### Isolated regression checks for deterministic Recent

Run these checks serially in an isolated test environment against the installed
application package and its built frontend. These instructions do not establish
that checks have run. Do not use a production service or model/SDK runtime.

1. Run the owned backend regressions:
   ```sh
   "$AMPLIFIER_TEST_PYTHON" -m pytest -q tests/test_chat_navigation.py tests/test_approachable_shell.py tests/test_live_clients.py
   ```
2. Run the frontend unit source:
   ```sh
   cd frontend
   node --test tests/work-shell.test.mjs
   ```
3. Run the new real HTTP/SSE receiving-build harness:
   ```sh
   AMPLIFIER_RECENT_EXPECTED_PACKAGE="$INSTALLED_AMPLIFIER_WEB_PACKAGE" AMPLIFIER_RECENT_EVIDENCE=/test-evidence/recent.json node tests/recent-visibility-browser.mjs
   ```
   Set `INSTALLED_AMPLIFIER_WEB_PACKAGE` to the interpreter's actual installed
   `amplifier_web` directory. The fixture rejects a different import and records
   the resolved module path and Python executable. It does not rewrite import
   paths to a checkout. It uses one synthetic backend fixture and one browser context, no SDK/model
   runtime. Raw action receipts, DOM, screenshot and JUnit are retained beside
   the evidence path. Require 20→40→60→80→100→120→actual exhaustion; toggle at
   20 and 100; current rank 25 and distinct pins; read failures/explicit Retry;
   unknown-write reconciliation without replay; two clients/two modules/reload;
   keyboard focus, 44 px fallback, narrow/coarse touch, count shrink and progress
   anchor without programmatic extra loads; no mount/resize/hidden/inert/collapsed
   fill; bounded reload after expansion; and
   unchanged draft, attachments, Canvas, model/bundle and saved message under
   passive browsing. Fixture progress/settlement is a separate, synthetic event.
4. Keep syntax/AST/diff results separate from these unrun checks. Older browser
   fixtures which assert an eight-row Quiet Recent cutoff describe the prior
   presentation, not this requested 20-row contract. Their recency/admission
   invariants still need independent qualification; do not claim they passed
   unchanged or weaken those invariants to accept this source.

Compact workspace rows reserve a **New chat** icon immediately beside **…**.
Hover or keyboard focus reveals both; touch keeps New chat visible with a 44 px
target. It opens the existing unsent-chat setup for that row's full folder path,
not the active chat's folder or a globally selected workspace. The shared
`session.draft {workspace,workspaceId,location:{kind:"workspace"}}` action rejects
stale, invalid or unavailable registrations before changing the draft. Opening
setup creates neither a chat nor a model turn. Returning to the original chat
restores its text, attachments and saved Canvas; another client's chat is unchanged.

Workspace details offer **Pin workspace** / **Unpin workspace**. Pinned workspaces
appear in **Pinned** alongside chats, with the same drag grip, keyboard ordering,
and **Move up** / **Move down** controls. Empty workspaces can be pinned.
The compact **Workspaces** shortcuts and full workspace browser retain pinned
folders in their ordinary recency/name order. The workspace-only recent index and
ordering actions remain available. Agents use the
same `workspace.pin {id,pinned}` and `workspace.pinOrder {ids}` actions; ordering
requires every currently pinned ID exactly once, including unavailable folders.
`shell.query.pinnedWorkspaceIds` is the complete vector, not just the visible
shortcut page. The compact sidebar keeps its six-row limit; the full workspace
index retains its bounded pages. Search and folder browsing remain available.

These are durable app preferences stored with the existing state records,
checkpoint/export and SQLite backup lifecycle, not shared conversation files or
a new database. Pinning, unpinning and ordering do not select a workspace/chat,
change another client's filters/pages, acknowledge activity or start execution.
Renaming keeps the registration ID and its pin position. A missing folder stays
hidden but retains its pin, so it returns at the same position when available.
Explicitly removing a registration prunes its pin through the existing remove
action; it does not delete the folder or its saved chats. Pin actions themselves
do not remove registrations or hide history.

**Sort** offers **Recent activity**, **Newest created**, and **Name**. Recent
activity advances an ordinary human UI post once, when its exact input is saved,
before runtime acknowledgement or an answer. It then keeps that position while
the chat streams or runs tools, and advances again when the turn ends or needs
approval. Live working/error indicators still update.
The separate navigation timestamp is saved across reloads; history and diagnostics
retain the actual progress timestamps. Imported idle history retains its saved
activity time. Each chat-list instance stores its own sort choice.

The immediate-post exception uses the host's UI origin, not message role, text,
title, transport (`via`), or a cited source message. Agent, peer, scheduler, host
question and voice transcript inputs keep their existing behavior. A typed UI
correction to a live generation uses the same exception; stale targets reject
before insertion and held/refused steering rolls back only its own provisional
position. Queued/applied steering is an admitted post, not proof the model obeyed.

Input identity, previous activity field presence/values and an opaque admission
fence persist with the saved input and command receipt. A bounded per-chat chain
holds at most 16 unresolved promotions; another post is refused before insertion
until an admission settles. Removed inputs cannot resurrect through a successor's
rollback. Accepted/uncertain retained posts and ready/attention settlement
supersede earlier fences, even with equal timestamps. Separate progress keeps its
pending ready settlement. Failed/unknown delivery with a saved input remains
visible as a post with its delivery evidence; checking or exactly retrying the
original command/input neither promotes again nor automatically replays work.
Pins, explicit sort/filter/archive scope, selection, drafts and other clients'
presentation are not changed by this exception.

Refused steering bubbles remain history, but rollback uses the exact current
session/input/fence in that bounded chain, not the last saved bubble. Rejecting
two overlapping posts in either order restores the original stable/raw/pending
field values and presence. Separate inputs and progress retain their raw activity
and pending settlement; a later refused post cannot erase their timestamp.
Restart marks unresolved steering delivery unknown without treating that local
restart as terminal admission evidence. Its existing fence remains available
for an exact later held/applied observation. Already accepted or uncertain
retained admissions are not downgraded. No input is resent by restart.

### Manager qualification checklist for refusal rollback

These are authored regression expectations, not claims of executed acceptance.
Run serially in the qualified isolated environment, with the same new test source
against the original candidate and corrected candidate:

1. Run the retained-bubble counterexample and refusal/restart matrix:
   ```sh
   "$AMPLIFIER_TEST_PYTHON" -m pytest -q tests/test_message_delivery.py::test_retained_held_steering_refusal_chain_restores_exact_activity
   ```
   The original source must fail the unequal-clock, B-then-A, no-restart,
   present-fields case: baseline `10/10/False`, A promotes to `20`, B to `30`;
   after both held refusals it leaves `10/20/True`. The corrected source must
   restore `10/10/False` with BOTH refused bubbles still saved. Keep this
   expectation unchanged. Also require both orders, equal clocks, restart between
   refusals, absent keys, persisted receipts/fences and passive duplicate/check
   behavior.
2. Run the boundary and real-service preservation suites:
   ```sh
   "$AMPLIFIER_TEST_PYTHON" -m pytest -q tests/test_chat_navigation.py tests/test_message_delivery.py tests/test_collaborative_workspaces.py
   ```
   Require newer accepted/failed/unknown posts, fresh C, independent
   peer/agent/scheduler/host input, raw progress between posts, ready/attention,
   exact-ID/fence rejection and original presence/values. The legacy boolean-only
   progress chain conservatively retains unknown raw ownership.
3. Run the existing packaged HTTP/SSE browser fixture without weakening its
   promotion-before-ACK, eight-row cutoff, progress stability, duplicate-input,
   other-client draft/reference and ready/attention expectations:
   ```sh
   cd frontend
   AMPLIFIER_HUMAN_POST_ONLY=1 npm run test:sidebar-activity-browser
   ```
4. Review the exact integrated candidate and these observed outcomes before a PR.
   Syntax/AST/diff checks alone qualify only a source candidate; they do not
   establish deployed behavior or satisfy the conditional acceptance.

New pins append to the shared list, independent of sorting. Existing installations
initially retain chat pin order followed by workspace pin order; subsequent mixed
ordering is saved durably. Re-pinning an already pinned item does not move it.
Drag a pin's grip to reorder it, focus the grip and press Up/Down (or Alt+Up/Down), or use
**Move up** / **Move down** in its details. Reordering preserves pins outside the
current search, workspace, or page. Agents use `navigation.pinOrder` with the complete
`pinnedItemIds` vector returned by `shell.query`, using `session:<id>` and
`workspace:<id>` identities. The legacy `session.pinOrder` and `workspace.pinOrder`
actions reorder only their own kind's slots without moving the other kind.
Pins and Settings orders use the same `ReorderList` pointer/keyboard control:
full row preview, placeholder, insertion marker, scroll assistance, focus return,
and cancellation with Escape, pointer cancellation, or an outside drop. A concurrent
order change cancels a held drag. Pins persist on drop; Settings preserves its
draft-only preview and explicit Save/Cancel. Distinct pin gestures made while a
previous save or display is pending remain in the local preview and are submitted
in order after each confirmed display. At most 32 moves wait locally; a full queue
shows backpressure rather than accepting more gestures. They never derive a command from a stale
page. A rejection, unknown save outcome, changed view or conflicting order drops
later unsent moves and reports that they were not sent; it never retries a save.
A saved order whose display is unavailable offers a bounded read-only refresh,
not a reconnect or mutation retry. Settings keeps its single-flight draft behavior.

Rows reserve their action and activity space. Names and parent labels truncate
within a fixed 60 px row (52 px with the existing compact shell density). Hovering
for 550 ms opens details without changing the row geometry. The full title,
selectable/copyable path, session ID, and pin/rename/remove actions are also
available through the keyboard- and touch-accessible **…** button. Escape and
outside clicks close the flyout; explicit open/close restores focus. Changing the
selected chat, workspace, or main browsing surface also dismisses existing details
without moving keyboard focus. A fresh peek may still inspect another row without
selecting it, and unrelated activity updates keep its details open. A short
pointer grace period lets people move from a row into its details. Saved skins
continue to supply colors, while the structural row rules prevent old wrapping
and hidden-action styles from reintroducing the layout problem.

Working, unresolved approval/error, and unread-response states are distinct.
Verified dependency waits have a static clock, not a pause icon or an execution
spinner. The selected chat shows a linked waiting target, or a count and local
disclosure for several targets. Expanding does not select a chat or send a draft;
only clicking a target uses normal chat navigation. Workspace activity keeps
waiting separate from attention. A stopped or explicitly paused owner is not
described as waiting; required questions, approvals and errors retain precedence.

This is a passive projection of an agent-recorded dependency block and exact
pending direct-request subscriptions. It is not an inventory of workers. A
bounded typed task projection identifies the pre-block epoch without changing
continuation authority. Existing saved source-generation/input identity prevents an
old wait from reappearing after new input, runtime replacement, or Stop. A derived
scalar index reads at most 17 current request references, showing at most 16 with an
explicit continuation marker; historical receipt bodies are not decoded.
The index is populated once when introduced; command insert/update/delete triggers
maintain it in the canonical command transaction. It contains no request/report
bodies and has no execution authority. Navigation does not parse retained runtime
task history; keyed directory task facts are refreshed by the task observation owner.
Recipient lifecycle publication visits only its indexed pending dependants so
their displayed status can refresh; this work grows with actual dependants, not
all retained chats or receipts.
Missing legacy identity/provenance remains **Work blocked**, never a guessed
target. A staged or returned report leaves the live-wait view; that does not
qualify success or resolve the task. Target errors and unavailable targets remain
explicit. Workspace and chat rows share icon/text columns; pin/unpin remains in
the details menu, without a redundant inline workspace pin.

Elapsed time comes from actual conversation activity, refreshed every 30 seconds;
unknown timestamps display a dash. Pending approval titles, tool arguments,
provider errors, and transcripts are not copied into the navigation projection.
Working and attention filters run on the complete matching library before paging,
so counts include conversations beyond the current page. These predicates,
ordering, and counts run against the shared indexed history directory. Only the
requested page is decoded; a dormant chat can appear in search, pins, workspace
counts, or worker history without becoming a resident session. Independent client
selection and Canvas state survive working-set eviction; explicit selection or
execution uses the common keyed admission path.

Full paths remain the workspace identity and search target. Compact labels use
unique path suffixes from the complete available registry; equal folder names
retain the ancestors needed to distinguish them. Folder selection and browsing
remain separate for a workspace that contains nested workspaces. Missing folders
remain hidden without removing their registrations or saved histories.

Registry path labels are cached by the complete set of paths, with a bounded
cache and a fresh result per caller. Activity changes do not recompute suffixes;
adding or removing a registry path does. Progress projections also avoid creating
workspace summary dictionaries for each chat already grouped under a workspace.
Keep these catalog loops allocation-light: small per-row costs multiply across
large libraries and every active client. The active-client performance gate
exercises these paths with 22,915 sessions, four browsers, and a Terminal stream.

Attached-client geometry updates (`navExpanded`, pinning, dimensions and Canvas
control disclosure) save only the client's presentation and command receipt.
They reuse unchanged catalog projections and notify only that client; they do
not rewrite conversation or artifact storage. Pending runtime progress retains
its normal scheduled publication. Mixed view changes, drafts, and legacy actions
without an attached client continue through the existing shared action path.

## Shared actions and shell modules

The `builtin.workspaces` and `builtin.chats` instances keep their public host,
instance IDs, capabilities, independent state, and bounded projections. When the
standard workspaces instance is paired to chats through its existing `hideWhen`
relationship, the two builtins present the shared drill-in layout. Standalone and
custom modules retain their independent composition.

Agents use the same `shell.view.update` and `shell.command` paths as the UI:

- Chat instance: `navWorkspaceList` (boolean), `navStatusFilter` (`all`,
  `attention`, `working`, `unread`), `navSort` (`activity`, `created`, `name`),
  plus the existing scope, search, and page keys.
- Sidebar sections: `navSectionsCollapsed` contains zero or more of `pinned`,
  `workspaces`, and `recent`. `navPinnedPage` is a zero-based page index.
  `navRecentView` stores Recent's independent `navFilter`, `navSort`, `navArchive`,
  `navCollection`, `navLocationFilter`, `navStatusFilter`, and `navChatPage` keys.
  UI filter changes clear Recent's saved page. Agents send the complete Recent
  view object when changing it. Workspace chat filters retain the existing keys.
- `shell.query.sidebarNavigation` exposes bounded `pinned`, `recent`, and
  `workspace` pages, plus `recentView`. The complete native-history index remains
  authoritative; no workspace-specific chat membership list is stored. Existing
  `chatNavigation` and `homeNavigation` projections remain available to older
  clients. Saved `navSimple` is accepted for compatibility but no longer selects
  a different renderer.
- `shell.query.pinnedNavigation` exposes the combined 50-row pin page; each item
  has a typed `id`, `kind`, and bounded `row` summary. `pinnedItemIds` includes
  hidden and off-page pins. The original chat-only pinned projection is unchanged.
- Workspace instance: `navWorkspaceMode` (`recent`, `folders`), plus the existing
  folder path, wildcard search, and page keys.
- The bundled sidebar's **Last activity** slider owns the attached client's
  `view.navAge` (`24h`, `7d`, `30d`, `90d`, `1y`, or default `all`).
  Home/status cards, workspace chats, All chats/search, Recent, and workspace
  session counts use that same age before counting or paging. Pins bypass age;
  unknown activity dates appear under All. Folder registrations stay navigable.
  This does not filter transcript messages, Work tasks, Files, notifications,
  or change the selected chat, draft, read state, or runtime.
  Reload imports the former builtin list's age once (current list first, then
  the other saved choice), preserving unrelated filters and group-by choices.
  Write it through `view.update`, not builtin `shell.view.update` or a nested
  `navRecentView`. Independently authored modules retain their own view contract.
- `shell.command` for `workspace.select` or `workspace.create` on the paired
  workspace instance opens that client's associated chat list. It preserves the
  other clients' library views and the normal app command semantics.
- `shell.query` includes bounded activity summaries, full and compact paths,
  activity counts, and session identities. Clipboard copying is a local browser
  convenience over those same queryable strings.

Hover state and flyout placement are ephemeral browser presentation. Viewing
these summaries does not mark an approval, error, or completion as reviewed.

## Browser observations and refresh

The existing SSE stream carries state changes. Shell snapshots refresh when
navigation membership, stable activity order, status, or client presentation
changes; text deltas alone do not invalidate the shell. Hidden documents defer
shell refresh and view reports, and catch up on visibility restore or reconnect.

Automatic `/api/view` observations identify their `visibleTextScope` as
`interface`. They include controls, focus, draft values, selected text, geometry,
and client-owned content such as custom shell components. They exclude repeated
conversation text and elapsed activity labels that already exist in authoritative
session state. `sessionId` binds the observation to its selected chat. Explicit
`window.amplifier.getState().renderedView` inspection still includes full rendered
text. Reports stay single-flight, retain the latest pending observation, and
invalidate deduplication on reconnect so the fresh connection receives a report.

`npm run test:sidebar-activity-browser` exercises the packaged UI against real
HTTP/SSE with synthetic progress: stable ordering, sort/pin persistence, drag and
keyboard reorder, custom content, drafts, hidden clients, and request counts.
It makes no model calls. The separate active-client performance gate covers
four browsers and one Terminal stream together.
`AMPLIFIER_HUMAN_POST_ONLY=1 npm run test:sidebar-activity-browser` isolates the
24-root QuietSidebar post fixture from the large existing performance fixture.
It holds the runtime acknowledgement and answer separately, checks the actual
eight-row cutoff through HTTP/SSE, and retains the progress, duplicate input,
other-client draft/reference and ready/attention assertions. Set
`AMPLIFIER_TEST_PYTHON` to the qualified test interpreter.

## Session identity

**Session ID** in the flyout and conversation details is the shared native ID used
by Amplifier CLI and the session directory. Imported CLI sessions also have an
internal Unified record key; it stays unchanged for selection, pins, artifacts
and app commands, and is only identified as **App ID** in diagnostic details.
Unified-created sessions normally use the same UUID for both. Chat search accepts
the shared ID (including a prefix), as well as existing internal keys.

The CLI's `amplifier session list` is scoped to its current project directory.
Run it from the full workspace path shown in the flyout, or pass that path using
`amplifier session list --project /path/to/workspace`. A list from a different
folder does not determine whether this workspace's session was saved.

## Browser dictation

The composer microphone uses the browser’s speech recognition to append finalized
speech to the current draft. It does not send a message or start a voice call.
Sending, changing chats, leaving the page, or starting a call stops dictation;
late results cannot write into another chat.

Settings → Voice → Dictation controls visibility for this browser. Automatic shows
the microphone on supported desktop browsers and hides it on iOS and Android.
This is a platform heuristic: browsers cannot reliably report whether a keyboard
has dictation. Show and Hide override the default, but cannot add speech support
to a browser that lacks it. The browser may use an online speech service.
