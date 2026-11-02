# Chats without a workspace

A new chat can use **No workspace** or a user-chosen **Workspace**. Model and bundle selection are the same in both modes. Existing conversations and callers without a location retain workspace behavior.

## Storage and authority

No workspace is application-managed storage, **not a security sandbox**. The existing runtime, host permissions, file policy, shell authority, and network access still apply. This version does not add OS isolation, a container, or network restrictions.

The draft does not create a session, native history, or a folder. First submission uses the existing `session.create` and `conversation.send` receipt flow. Managed creation allocates `<app-data>/chats/<session-UUID>/files` with private directory permissions. An allocation marker beside `files` records ownership and the creation receipt. Retries verify the same marker and never overwrite unrelated folders or existing files. A symlink at an allocation boundary is rejected.

The explicit public location is `{"kind":"managed"}`. Internally, the runtime still receives its physical working directory in `workspace`, allowing existing native history, tools, attachment handling, and file previews to work. The native history identifier and session UUID remain stable across restart. The app does not register each private directory as a workspace.

Managed chats use app and shared global configuration, plus their explicit session model/bundle choices. They do not inherit project/local settings or routing files from the previously selected workspace, the app-data folder, or generated files. Draft default and provider probes use explicit global-only configuration before any folder exists. Location is included in draft cache identity so switching location cannot display another workspace's defaults.

## Navigation and lifecycle

Managed conversations appear in All chats and its No workspace filter. Workspace views continue to show registered workspace conversations. The location choice and unsent message remain client-local and survive reload; one browser's new-chat setup does not retarget another client.

Closing, archiving, and restart preserve managed files and history. There is no automatic file cleanup in this version. Fork/recovery creates another managed directory and copies supported history using the existing no-replay path. Original files and historical artifact references remain preserved in the original conversation; generated workspace files are not copied into the new folder.

Agents use the same `session.draft`, `session.create`, `configuration.defaults`, `providers.list`, `providers.models`, and `view.update` paths. For managed drafts, pass `location:{kind:"managed"}` and omit/empty `workspace`. Supplying both a managed location and a workspace is rejected. `session.create` without a location retains the existing behavior.

## Validation and deferred work

Backend tests cover shared actions and the agent bridge, lazy allocation, receipt retries, scope isolation, navigation, native history, restart, saved artifacts, recovery, and allocation collisions/symlinks. The packaged synthetic browser fixture covers draft controls and reload, attachments, first send, mobile layout, and All chats. Neither test path performs model inference or uses production data.

True process isolation and a dedicated restricted-network policy are deferred. A future implementation must define the execution provider, filesystem/network grants, secrets access, resource limits, and disposal policy before calling this a sandbox. Moving an existing chat between workspace and managed storage, copying generated files during fork, export/promotion to a workspace, and automatic retention cleanup are also separate lifecycle features.

## Permanent deletion

Only an explicitly managed top-level chat with a matching Unified allocation
marker can be deleted. Workspace conversations retain Archive and Restore;
`session.delete` no longer means hide/unregister. Independent forks and recovery
copies have their own managed ownership and are deleted independently.

The shared UI/agent contract is `session.deletePreview {id}`, followed by an
explicit user confirmation and `session.delete {id, confirmationToken}`. Preview
returns `result` with `id`, `title`, a ten-minute token/expiry, counts of the chat,
workers, files/bytes, attachments, artifacts and published shares, plus readable
removed/preserved scope. A preview does not grant an agent permission to delete.
A changed scope or stale token requires another preview.

Deletion removes the owned managed files, native root/worker history, Unified
runtime state, shared Foundation checkpoint, presentation records, private
unshared attachments/artifact bodies, publication links, owned output records,
questions, completed operations and local diagnostics/recall data. Other chats'
shared attachments and immutable artifact bodies remain. Independent copies,
external files, global saved memories, exported copies, previously delivered
remote diagnostics and existing backups remain. This is application deletion,
not a claim of forensic erasure of disk/WAL/backup media.

Active work, pending questions/approvals, naming, history indexing, uncertain
operations, another host's shared lock, a separate worktree, uncancelled
schedules, unverified worker ancestry, foreign history, a second root
using the managed folder, symlinks and special/mounted files refuse with a
concrete error. Cancelled schedules and terminal run evidence can be removed;
a schedule with an active/unknown run still refuses. Refusals preserve files.

Owned native descendants, including underscore-containing worker IDs, remain in
the deletion scope without being treated as shared root checkpoint addresses.
The managed root's execution lease and any compatible legacy worker checkpoint
leases remain required, even when no checkpoint exists. Stable native metadata
locks exclude cooperating naming writers through confirmation and recovery;
they do not replace execution ownership. Shared-history cleanup still refuses
worker-only selection and unsupported native identities; use the owning managed
chat's reviewed deletion instead.

File enumeration and resource retention checks run off the server event loop.
The app checks retention again after retiring an idle runtime, then stages only
reviewed inodes by atomic rename under its action lock. Shared resource creation
after that boundary gets a fresh file. No new conversation work is replayed.

A confirmed deletion journal/tombstone precedes filesystem cleanup. Interrupted
cleanup reports `deleted: true, cleanupPending: true` and a warning; restart
resumes the confirmed cleanup before hydrating saved views. Completed receipts
retain only ownership identifiers/location, not the chat title or content.
Tombstones stop native reimport, reuse of the deleted identity and old creation
command replay. Stable lock files can remain; they contain no conversation
history and are preserved for cross-host coordination.
