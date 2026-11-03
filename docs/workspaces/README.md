# Unified workspaces

A workspace is a named home for related conversations and files. Create one by name, or attach a folder without moving it. Saved chats come from the shared native Amplifier store.

The design direction was accepted on 2026-09-22 after the attachment flow was simplified. The vision describes the intended end state; contracts separate its promises and failure behavior. [Implementation status](IMPLEMENTATION.md) distinguishes code and evidence from remaining work.

- [Vision](docs/VISION.md)
- [Placement](contracts/placement.v1.md): roots, naming, attachment, retry and preservation
- [History](contracts/history.v1.md): native identity, discovery, compatibility and recovery
- [Experience](contracts/experience.v1.md): navigation, details, accessibility and shared actions
- [Context](contracts/context.v1.md): repositories, instructions and context provenance
- [Execution](contracts/execution.v1.md): folders, concurrent work and runtime bindings
- [Handoff](contracts/handoff.v1.md): integration, preview ownership and cleanup
- [Acceptance scenarios](notes/ACCEPTANCE.md)

## Using the workspace flow

In New chat, the Workspace picker offers No workspace, existing workspaces, Create new workspace, and Use an existing folder. Both setup flows return to the same unsent text, attachments, model and bundle. A workspace's own New chat preselects that folder. No saved chat or model turn is created until the first message.

Settings → Workspaces controls `settings.workspaces.defaultRoot`. An empty setting uses `<app-home>/workspaces` (`~/.amplifier-unified/workspaces` in a default installation). A Spark user can set `~/dev`; the server expands it using the server account. This changes future placement only. More options allows a parent folder override for one workspace. Display renames never move files.

Use existing folder takes a server folder path or uses the existing server folder browser. Ordinary work edits those files, visible from a terminal in that folder. Native CLI/TUI chats are discovered automatically. No repository, submodule, clone, branch or execution-mode choice is required.

The sidebar presents Pinned, Workspaces and Recent. All chats retains the existing search, archive, sort, activity and folder-explorer tools. Workspace paths are available in details; duplicate names are qualified, and Settings can show all paths.

## Shared actions

`worktree.inspect/create/attach` accept `repositoryPath` for a repository inside
the task's workspace, execution folder or an already associated ready checkout.
Relative paths use the execution folder. For clean checkouts, inspect with
`mode:"clean"` and carry the returned absolute `repository.root` and `expectedCommit`
into creation. This avoids reading unrelated dirty/untracked source files.
Legacy full inspection still supplies `sourceRevision` for snapshot-bound creation
and explicit `carry_dirty`; a Git commit is not a snapshot digest. This supports
several child repositories beneath a non-Git workspace; each checkout retains
its own source and Git identity. Chat → Advanced → Work & automation exposes
the same repository selection and shows each checkout's source.

Creating or attaching these checkouts preserves the current execution folder,
history and unrelated dirty files. Use the returned paths explicitly for
commands, imports and previews; automatic multi-repository dependency remapping
is not provided. Existing handoff remains the separate operation for moving
the task's default execution folder. Native children cannot mutate their root's
worktree placement or ownership through either dispatch entry point.

## Workspace starters

New workspace creation offers **Start from…**. **Blank** keeps the empty-folder
flow. **Development** adds repository guidance without forcing Git or a bundle.
**Amplifier development** adds the Amplifier, Core and Foundation repositories
as independent child checkouts. New plans inherit an explicitly saved Unified app
or shared Amplifier bundle default, without copying that choice into project
settings. If neither is set, this starter keeps its `anchors-amp-dev` workspace
fallback. The currently selected workspace's override does not choose the default
for a different new workspace. Blank and Development continue to inherit defaults.

**Settings → Workspaces** keeps ordinary placement settings approachable.
Its **Manage starters** link opens a separate advanced list/detail editor,
reusing the routing editor's collection primitives. Basics come first;
repositories, guidance/working memory and resource tracking expand on demand.
Built-ins are read-only. Create a custom starter or duplicate an existing one,
then edit its description, instructions, optional bundle default, repository
URLs/folders/branches and resource tracking. Deleting a definition never deletes
created workspaces. Edits apply to future plans; prepared plans retain their exact
starter snapshot.
Amplifier development revision 6 makes this choice when the plan is prepared.
An inherited plan stays inherited if defaults later change; new chats use the
then-current defaults. Already reviewed revision-5 plans keep their explicit
bundle, as do saved custom starters. Duplicating a built-in copies its current
choice when duplicated, including inheritance, from a fresh server snapshot.
The returned copy is loaded into the editor; a previously displayed choice may
be stale. Duplication does not create or change a workspace. Explicit chat choices
and existing conversations are unchanged; no workspace settings are migrated.
Bundle choices share the chat dropdown's catalog: the preconfigured `anchors`
and `anchors-amp-dev` roots from Amplifier Foundation and `work` and `work-amp-dev` from
amplifier-bundle-work, plus enabled standalone bundles added through Advanced
settings. Behaviors and stale cache entries do not become starter choices.
These four roots are available on fresh installs and after upgrading Unified;
sources and dependencies are prepared on use, not eagerly during package installation.
Component updates prepare and qualify all currently offered roots. Existing
bundle defaults, custom source bindings and explicitly disabled roots remain unchanged.
The default may instead remain inherited. Unregistered names and URLs are not
accepted; existing conversations and workspace settings are not rewritten.
Prepared definitions are retained unchanged, but allocation refuses a bundle
that has since been removed or disabled. Review a new plan rather than silently
substituting another bundle.
For an added standalone root, the reviewed source registration is retained in
the new workspace so project/private additions still resolve there. Only the
chosen bundle binding is carried over, not provider credentials or settings.

Development scaffolding creates root `AGENTS.md`, root `SCRATCH.md`, a small
`.amplifier/AGENTS.md` entry point and optional `.amplifier/settings.yaml`, only
when absent. The entry point loads root guidance, whose relative scratch include
reloads working memory on each request. Existing instructions and memory are
never overwritten; existing workspaces are not retrofitted on app updates.
Provided Markdown prose is one source line per paragraph so plain-text viewers wrap naturally. Headings, tables and the indented workspace layout retain structural breaks. User-authored text and existing files are never reformatted.
Custom starters can enable working memory separately. Blank stays empty and
existing-folder attachment does not apply a starter.

The guidance restores the workspace-as-container model: project source goes in
child repositories, leaving the root available for plans, working scripts,
investigations and handoffs. Unlike the old temporary-workspace template, it
does not promise destruction at session end. Shared scratch is a short, attributed current-context index, not another task
ledger, event log, release history or authority source. Task state stays with the
host; detailed evidence stays with its owner and is read on demand. Ordinary
paths or Markdown links avoid automatically including archived history. Managers
promote only shared decisions and replace superseded entries, preserving useful
history and unfinished work outside automatic context. Cleanup is deliberate:
read current files, preserve concurrent edits and unresolved constraints, and do
not overwrite existing user guidance on an application update. Frequent source
checkpoints/worktrees belong to child repositories. Local workspace Git is
optional and enabled by default for **Amplifier development** only. Custom copies
expose **Initialize local workspace Git**. Enabled setup initializes an empty
`main` repository for root notes, with no commits or remote. All child folders,
local settings, common credential-file patterns and disposable logs are excluded
through repository-local rules and a starter `.gitignore`; explicit staging
still needs review. Existing Git metadata/ignore rules are preserved rather
than reinitialized. Root Git never replaces a project's independent history.
Interrupted initialization is retained and is not automatically repeated.
If a new allocation already contains a `.gitignore`, its contents are preserved
and setup requests review rather than claiming all root exclusions are safe.
Git ignore rules are a convenience, not a secret scanner; review explicitly
staged files before each commit.
No required submodules, tmux launcher
or automatic teardown is introduced.

Repository downloads run outside the app state lock. Setup details report each
repository's actual branch/revision, successful imports and failures. Explicit
retry only attempts unfinished steps; it never updates a successful checkout.
Setup does not execute repository hooks or configured filters, initialize nested
submodules, or install project dependencies; those remain explicit later work.
Interrupted or nonempty failed imports require **Inspect retained imports**;
inspection makes only local read-only Git checks, preserving user changes.
An import is time-bounded to 180 seconds with streamed output limits. If a timed
out import leaves an incomplete repository, inspection may not qualify it and
retry remains blocked. Preserve/review that folder and start a new workspace,
or repair it deliberately before inspecting again; no cleanup/reclone button
silently discards partial files.
Restart observes durable receipts without replaying setup. First-chat creation
waits for the instruction/default scaffold, not completion of every download.

Workspace Details exposes readiness, workspace bundle defaults and editable external resource inventory/observations. Settings → Workspaces contains only global folder/presentation defaults and starter management. Resource drafts stay attached to their explicit workspace while navigating the app and warn before reload; save or cancel them deliberately.
Resource records retain owner, status and evidence outside the working folder.
`active`, `reaped` and `observed_absent` are distinct: a saved observation does
not execute teardown or establish infrastructure state independently.

UI and agent callers share `workspace.starters.list/save/duplicate/remove`,
`workspace.setup.inspect/retry/reconcile` and `workspace.resources.list/add/update`.
Edits and status changes use observed revisions; all setup effects are scoped to
the reviewed creation plan.

Workspace Details also exposes **Resource reservations**, using the shared
`workspace.resources.reservations/reserve/release/uncertain` actions. A task can
reserve an agreed exact key exclusively or explicitly share it with other
holders. Matching keys conflict across workspace registrations in the same app
data home. Use a stable command ID for each mutation; retries return the saved
outcome, even after a later release. Updates require the observed revision.
`coordination.context` includes the first 16 current reservations and a cursor
for an explicit paginated read; it does not load peer conversations.

The host binds agents to their actual root or child identity; a child cannot
release its root's claim. A user can reconcile a retained claim with evidence,
including after its workspace registration disappears. Uncertain claims remain
held. Stop, restart and disconnect do not release claims. Release never performs
teardown or proves the underlying resource stopped. These cooperative claims
do not fence external writers, resolve differently named aliases, or coordinate
separate app installations. Inventory observations remain independent.

## Active work and shared file plans

Tasks and workers → Related work shows **Working in this workspace** for active
chats in the same canonical workspace. It distinguishes independent and
commissioned roots from observed child tasks, reports their execution folders,
and indicates overlapping declared paths. Use Refresh related work to update
that view or page through a larger active set. It never starts an idle worker.

Expand **Declare files for this chat** to share up to 16 file/folder paths. Folder
paths end in `/`; relative paths use the current execution folder. Saving an
empty declaration clears it. Unsent text stays in this browser tab through
navigation and reload. A stale save preserves that draft and the newer shared
record. Deliberately discard the draft to load the latest record before editing
again. A changed execution folder requires reviewing earlier declarations.

Agents use the same `coordination.awareness`, `coordination.intent.read` and
`coordination.intent` actions. Declarations require both observed intent and
execution revisions plus a stable command ID. Only a root can replace its own
declaration; a child cannot overwrite its root's plans. Ambient model context
contains a bounded current observation rather than peer prompts or drafts.
Exact shared declarations and additional active peers require explicit reads.

These are advisory plans, not file locks, access grants or stale-write guards.
Unobserved workers, idle declarations, external writers and unresolved filesystem
aliases remain outside the active view. Overlap does not block independent work
or impose a manager; coordinate, refresh the source or choose a separate copy
when the actual task warrants it.

## Placement actions

UI and agent callers use `workspace.list`, `workspace.prepare`, `workspace.create`, `workspace.add`, `workspace.select`, `workspace.rename` and `settings.update`. `prepare` returns a reviewed destination and a disposition: create, open, attach or blocked. `create` takes its `planId` and a stable transport command ID. A repeated plan cannot allocate a second folder, including after a lost acknowledgment. An interrupted allocation remains explicit and requires inspecting/attaching the existing destination.

Existing integrations may still use the legacy explicit-path `workspace.create` surface. The new UI uses prepared name-based creation and explicit attachment. `fromDraft: true` returns to the initiating unsent chat. Native IDs are accepted by chat actions, scoped with `nativeProject` when ambiguous. Legacy IDs remain compatibility aliases. History list/read responses expose `sessionRef` and `legacyId`; an unavailable discovery is reported as incomplete.

## Storage boundaries

`$AMPLIFIER_HOME/projects/<native-path-slug>/sessions/<native-session-id>/` remains the conversation source of truth. Workspace registrations contain location and display metadata, not a workspace membership roster. Native catalogs are rebuildable. Existing app presentation, voice, unsent inputs, artifacts and compatibility aliases remain intact; do not remove the app database as part of deployment.

Placement plans and receipts live under `<app-home>/workspace-placement`. They describe filesystem operations, not chat history. Existing worktree and execution-handoff services retain history home independently of the task working directory. Attaching a folder never silently creates or hands off to a worktree.
