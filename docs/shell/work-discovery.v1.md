# Work landing pages and sidebar contract — v1 (RATIFIED 2026-10-20)

These promises govern the standard web client's Home, All chats, All workspaces, individual workspace landing pages, and chat sidebar.

## Who builds against this

People returning to their work, designers, web-client builders, and catalog owners.
Shared meanings remain with [client experience](../../contracts/client-experience.v1.md);
state ownership with [client state](../../contracts/client-state.v1.md); resource limits
with [the working-set contract](../../contracts/working-set.v1.md); workspace actions with
[workspace experience](../workspaces/contracts/experience.v1.md).

## What it is

Home helps a person choose what to engage with. The sidebar helps them reach
familiar work or inspect its status while keeping their current context.

```text
Home: Focus / Brief / Studio + current search and scope
Sidebar: Home icon + Browse / Status in one row; New chat and Search chats
Browse: Pinned / Workspaces / Recent
Status: Needs you / Ready for you / Working / Pick up again
Choose a chat -> existing conversation and its existing action controls
Return Home -> remembered view, search, scope, reading position and Focus pass
```

## The promises

1. **Use the same work facts.** Both surfaces use the canonical catalog's
   attention, unread-response, current-execution, and other-saved-chat meanings.
   Read/unread actions reflect the current state and use the existing action owner.
   Broken: unread offers Mark unread, “Ready” implies accepted work, or idle means complete.
2. **Offer detail without changing authority.** Appearance supplies the
   starting view; explicit view choice and available space stay independent.
   Essential actions remain reachable under the [Appearance contract](appearance-contract.md).
   Broken: choosing Focus hides the only required action or changes permissions.
3. **Return without rebuilding context.** Home remembers its view, search,
   scope and pages; its reading position and Focus pass survive visits in the same tab.
   Sidebar mode, expansion and pages remain independent under client-state ownership.
   Broken: returning or switching sidebar mode resets the person's other surface.
4. **Keep navigation consistently available.** Browse/Status shares one row;
   the header owns its toggle; sidebar Search and New chat remain available on Home.
   Both sidebar views reuse chat controls and honor the same display preferences.
   Home is a recognizable header destination elsewhere and an icon in the sidebar’s first row. The current Home header has no redundant Home text or stale chat highlight.
   Broken: a view ignores workspace-label settings or loses common actions.
5. **Browse a pass without dismissal.** Focus offers previous/next, position,
   and restart across bounded pages of its group. Its cursor follows chat identity.
   Stepping never answers, approves, marks read, archives, or starts work.
   Broken: skipping an item disposes of something the person meant to revisit.
6. **Make scope a truthful filter.** Workspace scope uses registered workspace
   identity and existing creation rules; filtering never moves work or changes access.
   A pinned or unavailable scope cannot silently widen or create elsewhere.
   Broken: the person acts on another project after a delayed or failed query.
7. **Keep unknown distinct from empty.** Initial loading, failed, unavailable,
   and empty results remain distinguishable; counts describe the current scoped query.
   Routine refresh keeps usable content silently, without inserting status text or shifting it.
   Cards open existing chats; stale rows never acquire a newly selected scope.
   Broken: a refresh makes content jump or a failed read says nothing needs review.
8. **Pay only for needed discovery.** Use the existing bounded catalog;
   identical queries share its projection and hidden/closed Status groups release demand.
   Browsing reads no transcripts and starts no dormant worker or added model/summary job.
   Broken: merely opening this UI adds execution or work proportional to all history.

## Shared landing presentation

Home, All chats, All workspaces and an individual workspace reuse layout selection,
display preferences, filters, focus navigation and presentation primitives. Their
catalogs and action owners remain authoritative. Each destination remembers its
layout and reading position independently. Focus steps through bounded results
without changing their read state; Brief provides summaries and Studio exposes a
denser collection. All layouts retain meaningful empty, unavailable and error states.

The workspace's **Chats** tab reuses Home's status-grouped cards and Focus / Brief /
Studio controls, pinned to that workspace even when Home has another scope. Its
single chat toolbar sits below the workspace tabs; there are no workspace-wide
layout or Display controls. Keep the workspace title and tabs, without a nested
Home introduction, workspace banner or scope picker. Work, Files and Details remain
separate tabs. Home and workspace Chats retain independent search, status filters,
pages, reading positions and Focus passes; returning to one does not reset the other.
The view choices keep their names and accessible **Work view** group, without a
visible **Layout** label.

Keep search, sorting, archive/restore, pinning, rename/remove, folder browsing,
workspace creation/attachment, chat plans, Files, Details and Workspace work where
applicable. Reuse existing controls rather than recreating their mutation logic.
An active status filter stays visible beside the results; clearing it preserves
the search and workspace scope. Layout selection stays recognizable across
Appearance detail levels; screen width may change its arrangement.

The **All chats** catalog offers **Recent / date**, **Workspace** and
**Status** grouping, independently of Focus / Brief / Studio layout. One visible,
keyboard-operable **Last activity** slider in the sidebar selects **24h, 7d, 30d,
90d, 1y or All** for the attached client; All is the default. It applies to Home,
workspace Chats, All chats/search, Recent, Status and workspace session counts
before counting or paging. Age uses meaningful navigation activity, not selection,
rename or intermediate tool progress. Finite ranges include the cutoff, exclude
unknown/nonpositive dates, and retain pinned chats in their saved order. Pins
still honor search, archive, status and workspace scope. Unknown dates remain
discoverable under All. Grouping and age apply to the complete eligible catalog
before bounded paging; the existing sort applies within each group. Changing
either starts at the first page. Grouping remains local to the originating list;
age is shared by that client's discovery views and survives reload without
changing another client, canonical history, the selected conversation, draft,
read/attention state or runtime. Folder registrations remain navigable with no
matching chats. The former builtin list age is imported once on attach/reload.
Home-style workspace Chats keeps its independent search, status filter, pages and
reading position while honoring that same sidebar age. The workspace catalog
query retains grouping support for its existing consumers; the bundled workspace
tab no longer renders that list.

Use **Workspace** for the registered folder-backed entity and **No workspace** for
a standalone chat. “Project” remains ordinary language for someone’s undertaking,
not an alternate UI name or a new membership model.

Mixed pins distinguish a workspace destination from a chat: pinned workspaces
keep a recognizable folder icon and a small **Workspace** subtitle even while
showing activity separately. Location is secondary: show the shortest distinguishing
folder context for duplicate display names, considering the indexed registry
rather than just the visible pin page. The explicit full-path preference and
inspectable/copyable full path remain available. Mixed pin order and actions stay
with their existing owners; a subtitle does not change membership or execution.

A new web view without saved client state already starts on Home. Restoring an
existing view, refreshing or reconnecting retains its place; explicit chat links
keep their target. Validate these existing boundaries before changing startup.
Related-work exploration reuses explicit Workspace work inspection; no inferred
dependencies or new summary processing is authorized by this presentation change.

## Not in v1

- Logical projects spanning folders or moving chats between them. Promote after
  a separate membership and preservation proposal is accepted.
- New decision/deliverable records, review screens, lifecycle, or classification.
  Promote only when a demonstrated journey needs them and their owner is agreed.
- Fixed typography, breakpoints, column counts, or permanent card arrangements.
  Revisit through comparative review while preserving the promises above.
- Additional inference. An existing summary's input/output may be adjusted
  without extra calls; more processing requires a separate cost/scope decision.

## How the kit checks it

- Status meaning and uncertainty: seed attention, unread, working, idle, failure, and delayed-query cases;
  compare both surfaces with canonical facts, including read/unread actions.
  Delay a refresh and assert no new refresh text, row displacement, or loss of controls.
- Detail and navigation: exercise all views and both sidebar modes with keyboard, enlarged text,
  narrow screens and forced colors; verify shared row preferences and return focus.
- Return context: leave while typing a search, visit a chat/project, return and reload;
  compare Home, sidebar, draft, and Canvas state independently.
- Temporary passes: pass over an item, cross a page, reorder/remove an item, return and restart;
  verify attention, read state and runtime counters stay unchanged while stepping.
- Project scope: race scope/pin changes and unavailable projects with reads and creation;
  assert existing identity guards refuse incorrect targets.
- Bounded discovery: observe requested resources and runtime counters with hidden/closed groups;
  inspect query reuse and the absence of added inference in the changed paths.

## Open questions

- Which labels best distinguish unread responses from work ready for acceptance?
- How much surrounding context helps each view with a person's real work?
- How should related work be presented using already recorded relationships?

## Changelog

| Date | Change | Evidence |
| --- | --- | --- |
| 2026-11-09 | Reuse Home's chat presentation inside workspace Chats, remove workspace-wide appearance controls and the visible Layout label. | Product-owner narrowed implementation request; qualification remains separate. |
| 2026-10-20 | Extend shared landing presentation, add sidebar Home, restore Workspace terminology, and clarify layout/filter controls. | [Follow-up direction](../ux/decisions/20261020-landing-pages.md). |
| 2026-10-20 | Adopt the Home/sidebar promises, with plain names and quiet refresh/shared-row corrections. | [Product-owner decision](../ux/decisions/20261020.md); implementation evidence remains separate. |
