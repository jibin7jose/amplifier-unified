# Workspace Experience Contract — v1 (ACCEPTED DIRECTION)

How users find work, start simply, and inspect deeper details without disturbing a chat.

## Who builds against this

The navigation and conversation interfaces render the workspace services.
Information workers, developers, assistive-technology users, and agent clients
consume the same identities and actions through different levels of detail.

## What it is

The sidebar locates conversations and workspaces. Contextual details locate
outputs, sources, helpers, changes, and previews belonging to the current scope.
Viewing a detail is not an execution or authority-changing action.

```text
Browse organization
  Pinned       selected conversations                  (collapsible)
  Workspaces   registered workspace → full chat list   (collapsible)
  Recent       unpinned conversations, all locations   (collapsible)
Status organization
  Existing attention / unread / working / other saved chats

Chat details → Outputs · Sources · Agents · Changes · Preview
Workspace details → Files · Chats · Directions · Settings
```

## The promises

1. **First use stays concise.** Global New chat offers No workspace, existing
   workspaces, Create new workspace, and Use existing folder in one picker.
   Creation/attachment returns to that unsent draft with the workspace selected.
   Attachment asks for a folder, never a developer mode or a redundant preview.

2. **Navigation uses shared controls.** Browse groups Pinned, Workspaces, and Recent in shared collapsible sections. An alternate Status
   organization uses existing chat activity, not another work store; the web
   [Home/sidebar contract](../../shell/work-discovery.v1.md) owns its presentation.
   One rich chat row renders pins consistently; one workspace explorer provides
   recent folders, folder browsing, and chat drill-in. Pins remain excluded from
   global Recent and present in their workspace's full list. Helpers stay with their parent chat. Additional
   Browse filters use disclosure; collapse state persists independently.
   Workspace chat counts and activity summaries include only unarchived root
   chats, and refresh when a chat is archived or restored. Workspace archive
   filters select workspaces; they do not change this chat-count meaning. Archived
   chats remain available through the chat archive filter.

3. **Details preserve conversation state.** The existing Canvas hosts inspection
   while retaining its tabs. Opening a source, output, helper, or preview leaves
   the user's draft and execution scope unchanged. Sending an inspected item as
   context requires a separate action; passive browsing cannot send a message.

4. **Advanced controls reveal actual state.** Developer details expose full paths,
   repositories, revisions, owners, and bindings from authoritative records.
   The simple summary and detailed view cannot disagree about what is running.

5. **Actions share one authoritative path.** UI, TUI, and agent operations use
   the same service permissions and receipts. Cross-chat steering requires real
   user authority; toggling developer details grants no extra capability.

6. **Attention describes a concrete event.** Working, unread result, needs input,
   failure, and unknown effect have distinct accessible labels. Parent activity
   does not imply a helper finished, and a stopped run does not imply rollback.

7. **Small screens keep essential actions.** Narrow layouts replace side panels
   with a focusable full-width view and a return action. At 320 CSS pixels, create,
   inspect, stop, and return remain reachable without horizontal page scrolling.

8. **Available work stays distinguishable.** Missing folders are absent from the
   normal available-workspace list but reachable through history/recovery. Duplicate
   names gain a host/path qualifier. Full paths remain available in details.

## Workspace work and combined checks

The main workspace page and the canvas **Chat overview** expose **Workspace work**.
The conversation header does not add a separate work control. Opening either entry
preserves the selected chat and its draft. The workspace page loads twenty root
chat summaries at a time, only after its disclosure is opened; choosing a chat
inspects its work without selecting that conversation. An empty workspace needs no
chat selection. The canvas entry uses its current chat. Initial inspection reads
are bounded to the explicitly reviewed chat and its canonical workspace: active peers, actual shared or separate execution folders,
advisory file overlap, and requests needing attention. Independent peers remain
independent. Shared tasks and assignment controls are optional disclosures, and
inspection never starts or follows up a worker. Older rows load only on demand;
transcripts, exact identities and retained result details stay behind explicit
inspection. Workspace chat rows include an explicit Open chat action, including
commissioned helpers omitted from ordinary recent-chat lists. Only that action
selects the chat and closes the overview after accepted navigation; it uses the
existing per-client draft and navigation owner.

Combined checks use a repository picker and saved validation rows. Each selected
source expectation is the exact revision returned by an explicit inspection;
changing a picker or checking a saved candidate does not silently recapture it.
Commands still require an explicit Run action. Collecting selected runs observes
their native evidence without rerunning them. Unknown, pending, failed and passed
outcomes remain distinct. Manual JSON, receipt identities, import expectations
and publication declarations remain available under advanced disclosure. A lost
acknowledgement retains the same invocation identity and arguments for retry.

## Review before continuing

Workspace work offers an optional **Review before continuing** disclosure. Opening
it reads at most twenty saved root chats in the current workspace and preserves
selection, drafts and ongoing work. The same `coordination.review` action is
available to the calling root agent. Its cursor is bound to that source chat and
workspace; pages use stable directory order and replace the visible page rather
than accumulating the catalog. Each page is a new observation, not a frozen or
complete-workspace readiness certificate. Refresh returns to the first page.

The review identifies current observed activity separately from saved status,
pending decisions, settings/file-permission fences, saved execution placement,
bounded unresolved move observations, and saved response completion times.
Saved native permission waits without an observed live owner remain unverified;
exact chat admission reconciles them through the existing approval lifecycle. A
completion marker means a response finished; it does not mean its PR merged,
artifacts were independently checked, or a release was deployed. The source
chat's assignment owner supplies stale/unavailable claim decisions through its
existing rule. Old summaries missing the necessary authority stay unavailable
until their ordinary owner refreshes them; inspection does not hydrate history
to fill that gap. At most eight claims needing attention and four existing source
observations are shown. Claims remain responsibility, not filesystem locks.

Opening another chat, reviewing shared tasks, and reviewing source checks use
existing navigation and control owners. They do not enroll independent chats,
release claims, change settings, move files, rerun checks or send messages.
Current source and external PR state need explicit inspection. Installed app
identity is separate from loaded worker code; an optional explicit provenance
read observes an already running worker without starting or refreshing one.
Unknown and unavailable observations stay visible. Late replies from a closed,
refreshed or different chat visit cannot replace the current review.

## Not in v1

- **Custom sidebar sections** are promoted after users need organization beyond
  pins and workspace grouping; they remain an advanced extension.
- **Deep nested workspace hierarchies** are promoted by repeated demonstrated
  retrieval failures. Repository and agent trees do not become sidebar defaults.

## How the kit checks it

- P1: choose, create, attach, and cancel from New chat; retain its draft throughout.
- P2: pin/unpin and expand/collapse; compare visible identities for duplication.
- P3: open every details view with an unsent draft and inspect retained state.
- P4: compare simple/advanced projections with the recorded execution snapshot.
- P5: repeat creation and steering through UI and agent surfaces; compare authority.
- P6: inject working, unread, attention, failed, and unknown states; inspect labels.
- P7: keyboard-walk the 320-pixel and desktop layouts, including panel return focus.
- P8: remove/restore a folder and display two same-named workspaces on different hosts.

## Resolved decisions

Pinned conversations appear in Pinned and remain available in a workspace's full
chat list. Recent contains unpinned conversations across all available locations.
Through 50 matches it shows the whole list; above 50 it uses pages of 40. Its
filters/pages are independent of workspace drill-in and never filter pins.
Pins and Settings share the same reorder component, showing the full row while
dragging and preserving cancellation, keyboard ordering, and focus. Settings
retains its explicit Save; pin ordering saves on drop. Neither changes selection,
unsent drafts, session history, or activity timestamps.

## Changelog

| Date | Change | Evidence |
|---|---|---|
| 2026-10-20 | Apply the accepted Home/sidebar UX amendment. | [Product-owner decision](../../ux/decisions/20261020.md). |
| 2026-09-22 | Initial navigation and disclosure rules. | Live Spark PWA inspection, supplied sidebar/attachments screenshots, and the user's request for a simpler default experience. |
| 2026-09-22 | Simplify attachment and add the New chat workspace picker. | User's critique of redundant folder confirmation and developer options. |
| 2026-09-23 | Consolidate the two navigation views into shared collapsible sections, reuse Settings reorder, and page Recent at 50/40. | User's live sidebar screenshots and five requested adjustments. |

Design direction accepted by the user on 2026-09-22. Acceptance is not a claim of implementation conformance; see [implementation status](../IMPLEMENTATION.md).
