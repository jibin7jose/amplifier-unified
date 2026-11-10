# Bounded agent discovery and result delivery

The recurring host prompt supplies orientation, discovery and essential ownership
semantics. Detailed recipes ship in the `unified-app` skill and its task-selected
companions; the host does not inject that manual on every provider request. Optional
skill absence does not remove current app controls. Keep this split when adding
new controls, following the [workspace context contract](workspaces/contracts/context.v1.md).

`app_control list_actions` returns `items`, `prefix`, `revision`, `offset`,
`nextOffset` and `total`. Follow `nextOffset` with the same prefix and revision.
Pages are bounded to 12,000 escaped JSON bytes; a changed catalog rejects stale
continuations. The browser's action catalog is unchanged.

An action too large for one page returns `schemaRead` instead of a partial schema.
Pass those arguments to `list_actions`, follow `nextSchemaOffset`, concatenate
`schemaText`, and parse the complete JSON action before dispatch. These calls read
metadata only; they never invoke the action. Exact names, descriptions and schemas
remain available without an output-limit increase or silent truncation.

Permissions, providers, configuration, workspace, module, source and location
dispatches return their exact domain receipts plus compact state metadata. They
do not repeat the unrelated app overview. Use `get_state` with a JSON Pointer for
other UI state. The domain's own success, pending, denied and unknown outcomes are
unchanged; a bridge response alone does not establish domain completion.

The separate `tool-exec` module owns its selected-text limit. Its output-delivery
failure is distinct from an authoritative child execution outcome. A completed
mutation is never replayed merely to print less text. Large arbitrary domain
results still require domain-owned paging or retained receipts; this change does
not claim a generic persistent result store for every tool.

## Chat discovery and client navigation

`coordination.list` is the metadata-only title/ID finder. `query` filters before
paging; an exact `sessionId` resolves public/native identity with the ordinary
`nativeProject` ambiguity guard. Workspace is the canonical history association,
not the execution folder. Default scope remains the calling workspace; explicit
`all` includes registered own-history and managed roots. Archive state is visible
and defaults to all; browser Recent places and agent-created filters do not limit
this lookup. `history.search` remains full-text retrieval, not this metadata query.

Keep `directoryRevision` and identical filters with `nextOffset`; a changed
directory refuses stale continuation. Returned discovery coverage distinguishes
incomplete indexing from a confirmed empty lookup. Root lookup does not load
transcripts, admit dormant owners, select clients, warm runtimes or send input.

For user-requested navigation, `session.select` uses the originating connected
client or an explicit `clientId` displaying the calling chat. Missing, ambiguous,
disconnected or foreign targets require a choice; there is no global-view
fallback. UI-only reference proofs are not agent addresses. Ordinary navigation
and dirty-view guards still apply, and another client's selection is unchanged.

Its saved result binds source conversation, client, selected conversation and
selection revision. The compact bridge omits destination private state, even
after success; ordinary `get_state.selectedSessionId` still describes the calling
chat. Follow the receipt's `session.selection {commandId}` readback for current
selection metadata. Only the originating conversation may use that command
receipt. `changed`/`disconnected` are not confirmation; `selected` is host state,
not rendered browser evidence (`rendering:unverified`). An exact command retry
returns the original receipt without navigating again; a changed payload still
conflicts. Selection never sends input or prepares an agent.

Validation covers exact catalog reconstruction including oversized Unicode
schemas, stale catalog rejection, and the actual session-scoped permissions read
without repeated visible state. Tool-exec separately verifies a completed mutation
whose printed output exceeds 32 KiB and an unknown mutation under the same limit.
These are component checks, not deployed-worker or real-model acceptance.
