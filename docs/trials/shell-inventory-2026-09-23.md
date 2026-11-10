# Shell trial: feature inventory against main

Compared on 2026-09-23. Main was freshly fetched at `cf535c3d7afd701392e0d28646baca70a3aecf2b` (0.20.21). The isolated trial starts from `51dfcd5a92e450efcde90fbbe5f497348de8dab2` (0.20.20). This is a source and journey audit, not a claim that every integration has been exercised in the trial.

## Integration for release

The release candidate integrates main `f2286ab9` (0.20.22). The inventory below preserves the original audit; this section supersedes its outstanding integration notes.

- Chat actions now have one **Chat details** destination, containing naming, export, diagnostics/recovery and worker actions. The workspace search has one composite focus indicator.
- Main's single-viewer Canvas, file path copying, immutable versions and provider diagnostics are integrated without replacing their behavior.
- Call and screen-sharing controls now live outside the hidden conversation surface. Browsing during active work also exposes a scoped Stop action and Back to chat.
- Quiet navigation retains discovery/loading/error notices and refresh; a conditional issue notice links to All chats. App options and collapsed navigation expose aggregate unread attention, with Ready for you reachable from App options.
- The explicit Show paths preference applies to compact workspace shortcuts. Menu keyboard focus enters the options, Escape returns to the trigger, and leaving the menu dismisses it.
- Replacement navigation components remain in their registered slots. The built-in main-area browser explains its dependency and offers the existing standard-navigation recovery route if Chats is replaced. This is a compatibility boundary, not a promise to render third-party components as workspace pages.

The header conversation dropdown remains intentionally replaced by sidebar navigation and the full chat browser. Mockup-only features listed below remain out of scope. No new session-membership database or implicit shared-folder writer coordination is introduced.

## Changes made in the earlier revision

- Removed the header's duplicate Chat controls. Model, effort, bundle and Chat controls remain in the composer.
- Collapsing navigation removes it from layout, focus and the accessibility tree. The open-navigation button moves to the header. No hover rail remains.
- Removed Appearance from the cog menu. It remains a normal Settings page.
- New chat uses the visible workspace context. A workspace page or workspace chat supplies its folder. All chats / All workspaces have no selected workspace for this purpose and use managed chat storage. Returning to an unsent draft preserves its chosen location, model, bundle, text and attachments. An explicit workspace argument wins.
- The workspace picker separates No workspace, searchable registered workspaces, New workspace, and Use existing folder. Search covers names and full paths on the host; pages contain 40 results. It does not download the entire catalog.

## Recommended workspace chooser

Use a search-first list for switching between workspaces. A workspace's display name is the primary label, its parent path disambiguates it, and the complete path remains available on the row. Search can match an ancestor directory, so a deep folder does not require opening every ancestor.

Keep hierarchy in **Use existing folder** and **All workspaces → Browse folders**. These answer a different question: finding a location in the filesystem. Neither the picker nor physical directory nesting creates additional chat membership. Existing native histories remain authoritative.

The initial list is alphabetical, using the existing catalog ordering. A future recent/favorite ranking can improve it without making another workspace registry. Do not add a complete folder tree to the ordinary new-chat composer.

## Main capabilities: retained, moved or missing

| Capability in main | Trial status and access | Follow-up |
|---|---|---|
| Pinned, Workspaces, Recent | Retained as independently collapsible sections. Pins use the same component in sidebar and full chat browser. | Keep one implementation. |
| Pin reordering | Shared Settings reorder implementation retained, including the floating row. | Fresh populated drag/touch acceptance is still needed. |
| Pin metadata | Workspace subtitle and activity age intentionally hidden in compact rows; status and contextual details remain. Main shows more information inline. | Decide whether an optional expanded row is useful; do not duplicate the pinned control. |
| All chat search, archive/status/location filters, sort, pagination | Moved to All chats in the main pane. The 50/40 rule is retained there. Sidebar Recent shows eight shortcuts. | Keep full tools out of default navigation. |
| Workspace recent list, full-path search, nested folder explorer, counts, rename and remove registration | Full explorer retained in All workspaces. Sidebar shows six recent shortcuts. Workspace details have a smaller subset; removal is available from the full explorer's row menu. | Add a consistent workspace menu to the workspace page if this extra trip proves awkward. |
| Header conversation dropdown | Omitted. Sidebar and All chats replace it. | A keyboard quick switcher could replace its speed without restoring another permanent dropdown. |
| Native history refresh, discovery/loading and issue summary | Full chat browser retains refresh. Quiet sidebar retains errors but drops the discovery progress and issue-count summary. | Restore a small conditional status/attention affordance; avoid a permanent diagnostics block. |
| Section totals | Dropped from compact section headings. Full browsers retain counts. | Intentional simplification. |
| Folder path preference | Full browser honors path information; compact sidebar only reveals extra paths for qualified duplicates or full-detail presentation. The show-paths setting is not consistently reflected in shortcut styling. | Fix preference consistency before promotion. |
| Name-first create, custom default root, attach existing folder, collision handling | Reused, including draft-preserving cancellation and native-history discovery. | New picker changes presentation, not placement or history identity. |
| Chat attachments, model, effort, bundle, send/stop/voice | Retained in composer. Workspace chooser moved into the new-chat composer. | Live provider and voice checks remain unperformed in this isolated instance. |
| In-use input blocking and takeover | Existing generic owner metadata and shared ownership protocol retained. | No hardcoded client name. Live cross-client takeover not exercised here. |
| Rename/automatic naming, export, diagnostics/recovery, delegation | Existing details panel retained through Chat actions → Name and details / Export chat. | The same destination for those two entries can be made more direct later. |
| Fork/edit, retry and per-message actions | Existing message implementation retained. | Mockup's header “Continue in a new chat” shortcut was not implemented. |
| Archive/restore, delete, share snapshots | Existing chat row and Settings conversation/library surfaces retained; archive shortcut added to header menu. | Mockup's consolidated Share/export menu is not implemented. |
| Settings, themes, providers, bundles, permissions, memory, notifications, updates, repair | Existing Settings experience retained. Entry moved to sidebar cog. Appearance remains inside Settings. | Compare narrow-screen accessibility of the new cog menu with main's more mature overflow menu before promotion. |
| Feedback and settings attention indicators | Feedback form remains in cog. Some main header badges are no longer continuously visible; cog itself lacks an aggregate attention badge. | Restore meaningful attention visibility, especially when navigation is collapsed. |
| Ready conversations, task controls, goals, budgets, schedules, worktrees, handoff, portability | Existing advanced panels retained, principally through composer Chat controls and Settings Advanced. | These are relocated capabilities, not missing execution support. |
| Canvas tabs, file/web viewers, artifacts and renderer controls | Retained. Chat overview replaces the empty/library landing; Open menu groups file/website actions. Viewer options is a disclosure. | Validate all existing renderer workflows against the new surrounding layout. |
| Canvas hover/pin controls | Intentionally removed. Controls occupy space above the content. | Evaluate toolbar density at narrow widths; do not reintroduce content-covering controls. |
| Canvas split view | Still present under Viewer options because trial is on 0.20.20. Current main removed it. | Follow main's single-viewer decision when integrating. |
| Copy absolute/relative Canvas file path | Missing from trial; added by main 0.20.21 after its base. | Carry forward main's implementation and original-workspace path semantics. |
| Provider-check failure explanations | Trial still has 0.20.20 behavior. Main 0.20.21 adds safer, clearer diagnostics for subprocess/runtime preparation failures. | Carry forward the complete upstream change, not just explanatory copy. |
| Shell extension slots, custom components and recovery | Core slots remain, with app status/actions moved to the footer. The main-area browser specifically relies on built-in chat/workspace components. Recovery disclosure is hidden except in full-detail presentation; recovery URL remains. | Audit replacement-component compatibility, custom themes and recovery discoverability before promotion. |
| Active call / screen-sharing indicators while browsing | Existing chat subtree remains mounted but is visually hidden by the browsing surface; this also hides its call strip and sharing indicator. | Priority gap: keep live activity and Stop/End controls visible outside the chat before general use. |

## Mockup promises that are not complete product features

The earlier interactive HTML previews illustrated more than this trial implements. Their simulated results must not be mistaken for real backend behavior.

| Proposed experience | Actual boundary |
|---|---|
| User-curated workspace shortcuts | Trial uses six recent workspace shortcuts; no new favorites editor. |
| Independent workspace document home | Workspace Files can list folders passively, but preview requires an active chat in that workspace. No standalone document viewer or “New chat with this file” journey yet. |
| Rich Sources panel and clickable agent reports | Sources lists attachments from loaded messages only. Worker labels and access to existing history/coordination are present; unified source indexing and the mockup's report cards are absent. |
| Unified location/connection panel | Paths, native IDs, ownership and execution details exist across existing panels. The proposed compact connection card is not implemented. |
| New share / continue menu | Existing sharing, export and fork capabilities remain in their original detail/message surfaces. The proposed shortcuts are incomplete. |
| Focused / Balanced / Advanced as a coherent shell-wide choice | Existing detail/density/theme preferences remain. Trial applies only some disclosure changes; a full experience-level contract is not implemented. |
| Instructions, directions and repository bindings on workspace home | Not implemented by this shell trial. |
| Automatic safe coordination across repos/chats/agents | Not implemented. Same-session ownership is not protection against two different sessions editing the same files. Existing explicit worktree and handoff mechanisms remain. |
| One integration/preview owner, multi-repo combination checks, partial publish recovery | Still contract work, as already recorded in the workspace implementation document. |

## Priority before promotion

1. Preserve visible live-call/screen-sharing controls while browsing; restore conditional attention and native-discovery visibility.
2. Integrate current main, including single-viewer Canvas, file-path copying and provider diagnostics. Rebuild and validate the combined version.
3. Close shortcut-path preference, header quick-switch access and replacement shell-component compatibility gaps.
4. Exercise populated drag/reorder, dirty documents, blocked/takeover input, real provider/model catalogs, voice, mobile and custom themes. Then decide which mockup-only refinements merit implementation.

## Evidence and references

This revision passed 343 frontend unit tests, 55 targeted backend tests, and the production frontend build. New checks cover workspace/no-workspace defaults, agent/UI draft parity, preserved draft choices, deep-path search, bounded pages and collapsed-panel sizing. Browser and deployed-instance checks are recorded in the [trial runbook](approachable-shell-isolated-trial.md).

Source anchors:

- Header, composer and retained conversation: `components/web-client/src/main.jsx`, `work-shell.jsx`.
- Main sidebar behavior: `components/web-client/src/shell/navigation-components.jsx`, `workspace-explorer.jsx`.
- Settings and runtime access: `settings-navigation.js`, `settings-experience.jsx`, `runtime-settings.jsx`.
- Canvas: `shell-panels.jsx`, `canvas-workspace.jsx`, `work-overview.jsx`.
- Shared draft and chooser actions: `amplifier_web/new_chat.py`, `workspace_placement.py`, `service.py`.
- Original broader promises: [workspace implementation boundary](../workspaces/IMPLEMENTATION.md), [experience contract](../workspaces/contracts/experience.v1.md), [sidebar qualification](../workspaces/notes/SIDEBAR-2026-09-23.md).
- Earlier previews reviewed as references: `approachable-workspaces.html` and `unified-shell.html` in the task's visualization directory.
