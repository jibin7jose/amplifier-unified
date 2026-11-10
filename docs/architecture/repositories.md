# Repository and ownership map

Proposal supporting [CB](../../contracts/component-boundaries.v1.md).
Names marked **new** are proposed responsibility slots, not instructions to create
repositories. Compare retained candidate implementations before choosing a home.
Use existing ownership and public APIs where they fit. Independent repositories
may publish libraries, executables or optional capabilities; not all are services.

The independent web client's [temporary source delivery](../../components/README.md)
keeps a pinned copy in this repository while its owning repository awaits public
availability. The selection can switch to repository delivery without changing
the component's build or runtime boundary; this does not adopt it as the shipped client.

## Core boundaries

| Repository | Disposition and responsibility | Public seam / owns | Must not depend on |
| --- | --- | --- | --- |
| `amplifier-unified` | Existing; become family/distribution, installer, integration docs and release composition | Component manifest, installation and adoption receipts; family vision | Private copies of client/agent implementation |
| [`amplifier-client-web`](https://github.com/microsoft/amplifier-client-web) | **Extraction in progress**: current `frontend/` history imported; independent build and coordinated source handoff remain. Retained `amplifier-unified-client-web` is a separate AHP candidate | Browser persistence, UI, accessibility and transport adapter; current HTTP/SSE first, qualified AHP later | Python service objects, native session files |
| Shell SDK and authored components | **Proposed extraction** of `packages/shell-sdk`, authoring guidance and fixtures; components may release separately | Public slot/navigation/renderer contracts and validation | Built-in shell internals, app API singleton or private React contexts |
| `amplifier-app-tui` | Existing separate repo; evolve connected backend to AHP | Terminal interaction, local persistence and AHP; replace legacy CLI over time | Web rendering or native runtime in connected mode |
| [`amplifier-client-macos`](https://github.com/microsoft/amplifier-client-macos) | Existing; independently released Amplifier for Mac, with optional Tempo | Current HTTP client, native interaction, private local state, signing/updates; [desktop scope](../clients/desktop-clients.md) | Host internals, agent runtime, duplicated shared semantics |
| Windows desktop client (repo unassigned) | Planned; select native mechanisms and supported journeys before implementation | Shared DC/CX/CS behavior; platform-owned presentation and distribution | Mac-only frameworks, private host objects, copied normative contracts |
| Generic host owner | **Proposed extraction**; compare `amplifierd` and retained `amplifier-unified-host` candidate | Admission, routing, negotiated AHP/ACP, catalog/resource interfaces and lifecycle coordination | Amplifier core/loop imports, conversation bodies for routine routing, web widgets, Converge domain types |
| Conversation experience owner | **Proposed boundary**; Unified and external experiences can be different implementations | Shared transcript, participants, delivery identities and authorized retrieval; legacy projection preserves native history | Every agent's private working context or mandatory collaboration workflow |
| Native ACP adapter owner | Reuse/evolve the retained adapter where qualified; retain current Core/Foundation execution | ACP agent, native runtime composition, ownership and execution identity mapping | Web client, generic host private objects |
| Experience connectors / agent participation | Reuse Constellation connectors and adapter SDKs where their semantics fit | Conversation access and event delivery to an agent; runtime chooses context and memory strategy | A second canonical conversation transcript or one required agent memory layout |
| `amplifier-core`, `amplifier-foundation`, module/bundle repos | Existing; retain kernel and reusable native mechanisms | Native composition, persistence and ownership mechanisms | AHP UI concepts or product-specific catalogs |
| `amplifier-agent` | Existing separate building block; excluded from this migration | Its own independent product contracts | Native Unified execution; it is not our current or intended runtime |
| Catalog owner | Extract current native catalog discovery/index/query; compare existing candidates before assigning a repo | Rebuildable summaries, workspace existence and parent indexes; filtered paged reads | Agent initialization, transcript mutation, browser selection |
| Protocol extensions owner | Compare retained `amplifier-unified-protocol-extensions` candidate | Amplifier-only extension registry, permissions, versions, fallback fixtures | Forked upstream core protocol or UI implementation |
| Client helpers owner | Compare retained `amplifier-ahp-client-kit` candidate; only shared mechanisms | AHP SDK integration, local intent/cache interfaces, optional renderer registration | Host/global app store; duplicated upstream reducers |
| Interoperability fixtures owner | **New** independent seam fixtures, load/recovery probes and real-peer qualification | Replayable fixtures, capability matrix and exact evidence manifests | Production runtime ownership or shared user state |

The [retained distribution prototype](https://github.com/microsoft/amplifier-unified/blob/20f6bb078617f4d85d2f11932d103a69c8577fa2/distribution/components.json)
already names component packages. Its existence does not establish current parity,
public artifact availability or adoption. Resolve accessible source, packages,
maintainers and current contract coverage before declaring an independent lane
ready. Reuse tested mechanisms; do not establish competing generic hosts or call
existing HTTP/SSE AHP. Naming does not block contract/fixture work.

The client helpers owner can initially publish language-specific packages in one small
repo; split TS and Rust ownership if it helps teams. It owns recovery helpers and
client persistence contracts, not a framework that dictates all UI state. Upstream
SDKs keep their own schemas and reducers. Do not translate every action twice merely
to put an Amplifier name around it.

## Additional useful extractions

These are concrete follow-on boundaries, not reasons to delay the first useful
slice. Each becomes its own repo when its seam fixture is written; preserve an
existing suitable repo if the inventory finds one.

| Candidate owner | Current source / existing overlap | Boundary and independence test |
| --- | --- | --- |
| `amplifier-resources` (**new**) | `resource_files.py`, attachments, `amplifier_outputs/`, artifact storage | Stable resources, content/metadata access, version checks and auth; retrieve a large artifact without loading its agent |
| `amplifier-canvas` (**new**) | Canvas bridge/documents/views, frontend renderers and shell integration | Capability package and renderer SDK; ordinary chat works with it absent, agents invoke the same public operations |
| Voice/device capability owner | `voice*.py`, device dispatch; existing `amplifier-voice`, browser bridge | Reuse/evolve existing repos after comparison; media transport separate, exactly targeted permission and lifecycle |
| Configuration/composition owner | Shared settings, bundles and provider discovery; Foundation overlap | Native mechanisms stay Foundation; extract product admin/catalog UI capability, not a second settings authority |
| `amplifier-scheduling` (**new extraction or existing owner reuse**) | `amplifier_scheduling/`, schedules; compare `amplifier-drumbeat` | Durable schedule/trigger ownership and host admission interface; work runs with zero viewers |
| `amplifier-operations` (**new extraction**) | `amplifier_operations/`, durable questions/tasks | Durable operations API and capability; independent of a particular agent engine and frontend |
| `amplifier-publishing` (**new extraction**) | `amplifier_publishing/` | Publishing library/capability and resources; own tests without an app boot |
| Worktree/resource lifecycle owner (**proposed extraction**) | `amplifier_worktrees/`, `amplifier_web/task_environments.py` and their owning tools | Exact resource ownership, consumers, reconciliation and cleanup; archive does not destroy active work or review holds |
| `amplifier-portability` (**new extraction**) | `amplifier_portability/` | Explicit transfer/inspection and receipts; never implicit cross-engine conversion |
| Recall/history capability owner | `amplifier_recall/`, native history readers; Foundation and catalog overlaps | Search/read-only enrichment; index loss never changes native resume |
| ACP client bridge owner | Reuse an existing compatible bridge or propose a small separate repo | ACP server facing an editor, AHP client facing the host; no second history/authority, capability loss documented |
| Converge family repos | Existing Method, Agents, Queue, Service, Onramp, Direction, Operation, Experience SDK and bundles | Optional domain capabilities through generic host/client seams; no host imports of Converge-specific workflows |

A package is ready to extract when it can state its input/output contract, data
authority and independent fixture. Small size is not an objection. An extraction
that still imports a sibling's private source has not completed its boundary.

## Dependency direction

```text
distribution --> released components and compatibility manifest
web/TUI --> upstream AHP SDK + optional client helpers/renderers
host --> upstream AHP server behavior + ACP client + catalog/resource interfaces
conversation experience --> shared transcript + participation/connector interfaces
connector --> experience conversation API + agent's chosen runtime interface
Amplifier ACP adapter --> native runtime/Core/Foundation/modules/bundles
optional capability --> published host/resource/client-extension interfaces
legacy CLI --> native persistence + transitional discovery hint
interop --> public interfaces only
```

Keep native metadata naming/configuration authority in one place. The host's
display projection or catalog must not become another writer of a session's
canonical name. Make commands to change native facts go through their owning
adapter; then update index/projections from the confirmed result.

## First separations against current source

These observations are pinned in the [November 7 review](evidence.md#november-7-ownership-review).
They distinguish a package boundary from merely moving a directory.

| Source / boundary | Small reviewable change | Remaining design decision |
| --- | --- | --- |
| `packages/shell-sdk` and authoring fixtures | Publish the existing public API independently; install a fixture component from the built package | Package/repository home and supported interface revision |
| Built-in shell and appearance | Separate portable component/style artifacts from app-owned navigation, settings and compilation | Explicit callbacks/resource adapters before extracting coupled built-ins |
| Whole web client | Build/install against the current HTTP/SSE fixture and exact tested server | Independent public compatibility policy; later AHP mapping is separate |
| `RuntimeManager`, `WorkerRetention`, native adapter | Reuse existing root workers and idle admission checks for the vertical proof | Host-process failure survival and delegated-child isolation are separate choices |
| Native catalog/history readers | Define typed metadata and selected-page reads; compare existing implementations | Which bounded reader merits a Rust pilot, with native writers unchanged |
| Conversation and participation | Define ownership, authorship and delivery fixtures | Faithful multi-participant protocol representation; no invented standard fields |

Repositories, processes and languages are separate decisions. Extracting a library
does not add a daemon. Independent release requires a usable artifact and fixture,
not a permanent need to assemble sibling source checkouts. A Rust executable does
not remove Python prerequisites for native execution; the distribution must handle
remaining runtime dependencies if it promises a simple installation.

## How parallel work becomes practical

Each core repo ships a one-command local entrypoint, fixture peer, contract
revision, negotiated capability list and a minimal integration scenario. Local
development assigns an owned state directory and ports, with no shared checkout
edits. Core runtime tests use a fake model; actual peer/account tests are a
separate evidence tier. Contracts and extension schemas have one change owner.

The distribution consumes released artifacts rather than reaching into adjacent
checkouts. Cross-repo changes can carry candidate package references in isolated
integration receipts. Record actual versions at installation and fresh worker
start. Track latest qualified components and preserve previous locks as rollback
receipts; never confuse a source SHA with installed adoption.
