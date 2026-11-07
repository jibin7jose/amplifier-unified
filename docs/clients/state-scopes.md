# Client state scope map

Design note supporting [CS](../../contracts/client-state.v1.md), not an additional
contract or a statement that every synchronization channel exists today.
"Local" always needs a qualifier: execution host, workspace, device or client view.
The [storage design](../architecture/state-and-storage.md) adds concrete
client persistence, AHP recovery, catalog filtering and runtime-residency guidance.

## Proposed ownership and subscriptions

| State | Authority / scope | Who follows it | Client effect |
| --- | --- | --- | --- |
| Shared messages and participants | Experience conversation owner under [HP7](../../contracts/host-protocol.v1.md) | Authorized detail viewers and connected participants | Reconcile shared conversation; preserve local draft/scroll |
| Native progress/history, pending questions and execution ownership | Native execution and existing decision owners under [CS7](../../contracts/client-state.v1.md); host routes their projections | Authorized execution/decision consumers | Retain the participant/target and exact outcome; do not merge private execution history into the shared transcript |
| Name, activity summary, unread/attention source | Canonical conversation metadata and host summary projection | Interested catalog/attention views | Update small summary; do not load every transcript |
| Workspace registration, host paths, repository availability | Execution host | Workspace/catalog consumers | Identify the host; do not interpret a remote path as local |
| Global defaults and provider configuration | One host's selected shared configuration root | Settings viewers and dependent effective configurations | Recompute affected inherited values; no automatic cross-host sync |
| Shareable project defaults | Project configuration on host | That project's settings and dependent sessions | Respect higher-priority overrides |
| Private workspace-local settings | Host-local configuration for workspace | Authorized workspace consumers | Never imply that committing project files includes these values |
| Explicit session settings | Canonical session scope where portable, otherwise declared host capability | That conversation's viewers/runtime | Show override provenance and applied/pending state |
| Effective merged config | Derived from the supported scope chain | Consumers whose resolved inputs changed | Keep separate from persisted source documents |
| Mounted runtime config | Actual prepared worker | That conversation's viewers/operator | Show current applied revision independently of saved defaults |
| Catalog results and refresh status | Host cache/probe owner | Viewers of that catalog | Show freshness/updating state; a result is not a mounted or reachable provider |
| Selection, private per-chat/pre-chat draft, pending attachments, scroll, open panels | One independent client view, persisted on that client | That view only by default; explicit bounded agent inspection if needed | No routine backend mutation; never couple concurrent windows |
| Intentionally shared draft | Optional standard AHP chat draft | Authorized chat subscribers using the explicit shared feature | Initialize a clean editor; preserve dirty local input; debounce shared edits |
| Unsaved settings fields and ordering previews | Local persistent editor state | That editor; explicit nonsecret inspection where supported | Preserve across remote saves; expose a conflict instead of overwriting |
| Theme/layout preference | Explicitly declared shared preference or client override | Consumers of that declared scope | Do not assume all presentation preferences are private; current shared theme differs from local preview |
| Credentials, raw secret form values | Host/device credential store or transient local input | Secret-aware mutation path only | Publish configured/available metadata, never raw values in snapshots |
| Mic, clipboard, camera, notification permission | Specific device/client | Targeted effect handler | Permission and effect outcome stay local; accepted session content may be shared |
| Uncertain command/outbox entry | Originating command identity + client recovery storage | Originating client and host receipt lookup | Preserve exact retry identity; do not transfer it by copying a view |

Browser and TUI storage implementations differ. The current backend-persisted
private view records are a migration source, not the target storage location.
Use IndexedDB for meaningful browser drafts/outbox/cache, tiny preferences in
localStorage, and local disk for TUI. An agent-visible editor is not automatically
public to every client. Authentication remains separate from client IDs.

## Configuration example

On one host, project P inherits provider defaults from its shared root. Session A
inherits P's model; session B has an explicit model override. Two clients edit P.

1. The first saves a new model against the source revision it read.
2. Both settings viewers see the new persisted value and provenance. The second
   editor's unsaved changes remain intact and its stale base is identified.
3. A's effective configuration changes; B's model override remains effective.
   Other inherited fields are evaluated separately rather than treating the
   presence of any override as immunity from all project changes.
4. A mounted worker reports its current applied configuration and the host's
   supported apply point: future preparation, idle application, queued change or
   explicit restart. Saving alone never claims that A switched immediately.
5. A corresponding workspace on another host does not change unless a separate,
   explicit configuration distribution feature applies the same update there.

Provider priority, bundle composition precedence and per-role routing candidate
preference are different inputs. A client must not represent them as one order.
Current settings owners are documented in the [work plan](development-plan.md).

## Subscription example

Two tabs and one TUI view A; another TUI views B. All show the workspace catalog.
An assistant update in A produces A detail for its three viewers and a bounded
summary change for catalog consumers. It does not rebuild B's transcript or
unrelated settings. Closing A's three viewers removes their subscriptions, while
A keeps working and saving. Returning obtains a current snapshot before following
new updates. A slow viewer uses bounded buffering and upstream AHP recovery without
slowing A; arbitrary state-action loss or private reconciliation is not permitted.

This is a target for scoped publication. The pinned baseline performed broad
publication; [CS3's assessment](assessment.md) records that observation. A current
implementation and performance check is required before claiming this target.

## Bundled browser implementation

The bundled web client and server ship together. Their current transport is
documented in [Browser state transport](../browser-state-transport.md): shell
reads request visible navigation resources, while ordinary attached browser
frames carry `selectedContext` and request a bounded `subagentNavigation` page
only for an open worker-history dialog. Coverage and page scope are explicit;
omitted pages are not empty results. Independently released clients and explicit
complete-state APIs retain their separate contracts.

This removes duplicate navigation construction, but does not yet suspend
selected messages or Work details while another surface is open. It is an
incremental implementation of the subscription target above.
