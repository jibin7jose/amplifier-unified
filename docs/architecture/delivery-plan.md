# Delivery plan and parallel work lanes

Implementation work derived from ratified HP/AP/WS/CB and CS/CX/CD contracts.
The user approved the plan and direction on 2026-10-02 and explicitly excluded
`microsoft/amplifier-agent`; retain Core/Foundation and the modular native runtime.
Numeric budgets and repository names remain implementation choices; ratification
is not evidence that any runtime behavior is complete or shipped.

## Current main application work

The October 14 [bounded-data implementation plan](../../plans/2026-10-14-main-working-set-architecture.md)
records the approved performance sequence for the existing application. It
reuses these ownership and working-set commitments without making AHP/ACP adoption
a release prerequisite. Its concrete resource semantics and numeric budgets remain
proposals pending owner review. The waves below govern the separate protocol proof
and migration, not an automatic cutover from main.

The [prebuilt-release proposal](../design/prebuilt-release-artifacts.md) sequences
the existing updater's artifact support before removing generated frontend output
from Git. This packaging work preserves Node-free releases and is independent of
the protocol migration; unrelated releases do not wait for it.

## Sequence and gates

The November 9 refinement below defines the next small batches. The broader waves
and Q matrix remain migration qualification, not prerequisites for every package
extraction or unrelated main release.

| Wave | Work | Exit evidence |
| --- | --- | --- |
| 0: establish seams | Refresh ownership, finish full native control inventory, settle initial capability floor; create public fixtures, version matrix and performance traces | Reviewed interfaces, runnable AHP/ACP fixtures, independently reproducible baseline and proposed budgets |
| 1: build independently | ACP Amplifier adapter, generic AHP host, catalog/hints, web local-state/client seam and TUI backend | Each runs with fixture peers; no dependency on another lane's private checkout; bounded basic tests |
| 2: prove the architecture | Real web + TUI + host + native adapter; second ACP agent; independent ACP client; independent AHP peers | Core journey matrix, failure injection and large-catalog results; capability gaps named |
| 3: extract capabilities | Resources, canvas, configuration, devices/media, scheduling and remaining package boundaries | Public interfaces and feature-specific acceptance; no newly introduced host-native bypass |
| 4: migrate and release | Legacy classification/import, local draft migration, old transport retirement, compatibility and safe activation | Native history preservation, rollback drill, actual client/host/worker adoption; outstanding feature limitations visible |

Wave 0 should be small enough to unlock the independent lanes, not a design phase
that waits for every optional feature. Do not declare migration complete at Wave 2:
supported existing features require either qualified preservation or an explicit
documented product decision to retire them. Track that decision per feature.

## Launchable lane boundaries

Names are responsibility slots, not assignments to current people or sessions.
Before launching a lane, resolve current ownership and replace proposed paths with
the exact target checkout/files. Every lane receives the common brief below.

| Lane / clauses | Writable owner and initial source | Producer / consumer and dependency | Deliverable and falsifier |
| --- | --- | --- | --- |
| L0: interfaces/fixtures, HP1/AP4/CB3–4 | `amplifier-interop`, extension repo; this contract packet under one editor | Upstream schemas -> all lanes; first prerequisite is supported versions/capability floor | Minimal fake ACP agent + AHP host/client, failure fixtures; broken if peers need private implementation imports |
| L1: native ACP, AP1–6 | Adapter repo; extract from `runtime.py`, `runtime_worker.py`, `runtime_controls.py`, `execution*.py` | Native runtime -> standard ACP -> host and Zed; depends only on L0 schemas | New/prompt/stream/permission/cancel/load/resume plus complete limitation inventory; broken if a feature silently disappears or native history forks |
| L2: generic host, HP1–7/CS3–4 | Host repo; `server.py`, generic parts of `service.py`, command/ownership plumbing | ACP events -> AHP resources; use fake agent/catalog until L1/L3 ready | Two viewers, reconnect, receipt crash tests; broken if native imports or a global snapshot are needed |
| L3: catalog/hints, WS1–3/WS5–6 | Catalog repo; `automatic_history.py`, `history_watch.py`, `native_catalog.py`, history query; separately owned CLI/Foundation changes | Native durable facts/hints -> paged catalog -> host/agents; independent of L1 runtime completion | 4k/25k fixture, lost hints/rebuild, no agent starts; broken by synchronous historical scans or lost records |
| L4: web/local state, CS2–4/CX1–2/CB1 | Web repo; `frontend/`, persistence and AHP backend adapter | Upstream AHP + fixture host -> browser; depends on L0 only | Draft survives browser restart, zero typing traffic, scoped rendering; broken by shared private drafts or whole-app rebuild |
| L5: TUI/AHP, CS1–4/CX1–7/CB1 | Existing `amplifier-app-tui`; connected transport/persistence seams | AHP fixture then actual host -> real terminal; independent of web | Actual Ratatui transcript/approval/reconnect tests; broken if connected mode imports native execution |
| L6: extensions/capabilities, AP5/CS5–8/CB2 | One owned repo per capability from repository map | Standard config/resources first, negotiated extension only for gaps; after L0 interface review | Feature mapping + unsupported-peer fixture; broken if ordinary chat requires the capability |
| L7: independent integration, HP/AP/WS/CB4–5 | `amplifier-interop` and distribution manifests | Released/candidate public artifacts -> combined journeys; follows lane deliveries | Rerunnable real-peer and scale receipts; broken if only our default stack is exercised |
| L8: migration/activation, AP6/WS1/CD6 | Distribution migration tools and legacy bridge | Qualified components -> owned staging instance -> deployment qualification | Preserve/checksum history, import classification, rollback/fresh-worker receipts; broken if unknown resume causes replay |

Do not ask L1 and L2 to edit the same runtime extraction simultaneously. L1 owns
native behavior; L2 consumes its fixture interface. L0 settles any schema revision
before both implement it. L7's checker reruns public tests independently of authors.
After direct peer qualification, L7 can assess an ACP-facing/AHP-client bridge
for editors that should access hosted sessions but do not speak AHP themselves.

## Per-lane brief and evidence record

```yaml
lane: L1
status: implementation-active
contract_clauses: [AP1, AP2, AP3, AP4, AP5, AP6]
source_revisions: [exact-shas-before-work]
writable_repositories_and_paths: [resolved-owned-adapter-checkout]
read_only_dependencies: [upstream-acp-schema, native-runtime-contracts]
producer: native-Amplifier-runtime
consumer: ACP-host-and-independent-client
fixture_peer: versioned-ACP-client-fixture
acceptance: streaming-approval-cancel-native-resume-without-host-native-imports
falsifier: duplicate-execution-or-lost-module-capability
evidence_path: evidence/L1/<run-id>/manifest.json
independent_check: separate-reviewer-runs-the-public-entrypoint
handoff: exact-commit-package-version-capabilities-known-gaps
```

Evidence records include setup, commands, scenario, expected and observed outcome,
source/package versions, environment, raw artifacts and limitations. “Not run” is
not a pass. Record each ratified promise against actual implementation evidence; do not
convert the design approval into a conformance pass. Keep detailed work in lanes;
do not grow the concise contracts into implementation checklists.

## Required acceptance scenarios

| ID | Scenario | Evidence / falsifier |
| --- | --- | --- |
| Q1 | 2 browser tabs + 2 actual TUIs, viewing A/B with different drafts | Independent selection/input, one authority; any draft overwrite or wrong-target command fails |
| Q2 | Lose replies before/after admission and ACP dispatch; crash/restart host/agent | Exact command/native IDs, one accepted turn when established; uncertainty retained, never prompt replay |
| Q3 | Normal reconnect, replay overflow, lost/corrupt cache and missed catalog notifications | Correct snapshot/replay and catalog refetch, pending reducer reset, local intent reconciled separately |
| Q4 | 4,000 historical projects / 25,000 sessions, then 100,000 sessions with fixed active set | Filter before page/detail read; zero dormant agent starts and zero event-log parsing in catalog requests |
| Q5 | Root chat with large delegated tree, long transcript, big tool bodies; backfill; generic client omits view hint | Lazy children/body requests; standard state semantics preserved, memory measured; unresolved bound blocks strict claim |
| Q6 | Slow/uninterested/zero viewers; concurrent active work | Bounded queues/replay, unrelated details untouched, viewer loss does not stop execution |
| Q7 | Config inheritance/override with busy and idle agents; two dirty editors | Saved/effective/mounted revisions truthful; no lost local edit, no unrelated mounts |
| Q8 | Legacy native roots/children, external CLI owner, deleted/offline workspace, missing module | Resume/read-only/blocked classifications explicit; retained file checksums unchanged |
| Q9 | Copilot, current Codex ACP, Claude ACP through our host; Amplifier through Zed; AHP peer in each direction | Exact executable/version/auth/capabilities and real usage; README inspection alone fails the runtime gate |
| Q10 | Device/media and rich capability absent/present; same operation from user and agent | Correct targeted effect, fallback and authority; no blanket broadcast or replayed effect |
| Q11 | Each extracted core repo builds/starts with fixture peers | No sibling private imports, shared mutable checkout or live account needed for core development |
| Q12 | Upgrade, mixed compatible versions, rollback and new worker | Installed component and worker versions recorded; source/CI success alone fails adoption gate |

For Q4, vary projects and sessions independently (the earlier synthetic probe
varied both together). Example fixture: 4,000 historical project records, 100
existing directories, 25,000 total sessions, 1,000 relevant roots and many children;
then vary existing directories/root counts. Include missing/unknown parent metadata,
renames, out-of-order hints, offline mounts and concurrent catalog changes. These
counts are test data, not measured facts about the production host's relevant subset.

## Candidate budgets to measure and settle

These are proposed acceptance targets, not demonstrated performance or protocol
requirements. Record hardware, filesystem, network, p50/p95/p99, sample counts,
history sizes, active workers and client counts. Separate cold from warm paths.

| Operation | Proposed gate |
| --- | --- |
| Private draft/edit/expand/scroll | Zero backend traffic/writes; local feedback p95 under 50 ms |
| One scalar host read / one metadata update | No historical scans or whole-state copy/hash/checkpoint; direct resource access |
| Durable command acceptance on controlled local/LAN setup | p95 under 200 ms excluding cold agent start/model work; immediate truthful pending UI |
| First indexed catalog page | p95 under 200 ms on stated local fixture; default page 50, configurable bounded cap |
| First selected history page | Candidate p95 under 300 ms warm / 1 s cold local storage; no runtime initialization |
| Fixed active set, 25k -> 100k stored sessions | Explain indexed-query growth; no linear historical traversal on interactive updates |
| Cache and replay residency | Enforced byte/count/age budgets, documented defaults, no growth after churn settles |
| Concurrent clients/workers | Test 1/8/32 clients and 1/8 active sessions, plus one slow subscriber; measure CPU/RSS/queues rather than assume universality |

Derive concrete memory and idle-worker defaults from measured processes. A fixed
worker count alone is inadequate because provider/module footprints differ.
Instrument client event -> local paint -> host queue/admission -> durable write ->
ACP delivery -> resource publication -> subscriber receive/reduce -> paint. Report
model-first-token/cold-start separately so “send felt slow” can be localized.

## Safe migration without permanent dual architecture

1. Create versioned native identity mappings and rebuildable catalog side by side
   with current data. Initial index import is throttled, restartable and observable.
   Preserve all native files and keep missing-directory archives off ordinary lists.
2. Move private state into clients with a one-time read/import of existing
   client-scoped records. Existing local data wins; import ambiguous views only
   with a visible choice. Record successful import before stopping legacy writes.
   Retain a recoverable export until the migration window closes; no silent draft loss.
3. Add ACP/AHP routes under an explicit supported transport/version boundary.
   A legacy facade may serve old clients temporarily, but invokes the same host
   commands; it must not be the implementation beneath a supposedly incremental
   AHP endpoint. New clients never depend on the legacy full-state projection.
4. Classify old sessions: native-resumable after qualification, owned elsewhere,
   readable but missing a compatible engine/module, or invalid with a reason.
   Do not change their engine implicitly. If a format must break, retain read/export
   and describe the exact unsupported resume set before retiring it.
5. Cut over an owned isolated development instance with production-shaped synthetic
   history. Complete Q1–Q11 and feature migration accounting. Then qualify an owned
   isolated instance following its development guide; never use the production host as a lab.
6. Release compatible components, verify actual installation and fresh workers,
   and separately qualify production activation. Current work does not deploy.
   Roll back components only when their storage/capability compatibility allows it;
   preserve new native writes and mappings, never restore old snapshots over them.
7. Remove obsolete global-state paths and legacy transport after the published
   support window and adoption evidence. Until then, record its separate cost and
   users; do not claim it has the new path's scaling properties.

## Decisions deliberately left to implementation evidence

Host language/SDK choice, exact idle-memory defaults, minimum supported peer
versions, which advanced controls can use existing upstream extensions, and the
last unsupported legacy resume cases. None blocks L0 fixture work. Record their
resolution with evidence rather than silently inventing protocol fields or
discarding supported product behavior.

## Next bounded batches

Reordered November 9 by user direction: repository decomposition and the ACP pilot
are the primary development lanes. This supersedes their earlier secondary priority
for this work, while Main's current releases remain independent. The
[November 7 ownership review](evidence.md#november-7-ownership-review) remains the
starting map, with current source deltas qualified separately.
This sequence delivers independent development before a full protocol migration.
Each row is a separate reviewable change; no repository is created by this proposal.

| Batch / owner | Deliverable | Focused exit evidence |
| --- | --- | --- |
| 1a: native ACP execution | Enforce the existing Foundation preparation policy for an explicitly immutable worker, then exercise the retained adapter against current native execution | Truthful readiness, actual prompt/stream/checkpoint/load, with Full and active Stop qualified separately; no adapter bypass or copied runtime |
| 1b: independent current web build | Export the current HTTP/SSE client and its finite canonical build inputs as a disposable source capsule; build a separate static artifact with exact paired-server provenance | Build outside any Unified checkout and exercise a synthetic HTTP/SSE peer; no second maintained catalog/SDK owner or implied cross-version compatibility |
| 1c: independent peers and conversation fixture | Provide a one-command standard ACP peer/runner and a fixture-local experience owner with distinct participants and execution bindings | Stream/cancel, explicit missing capabilities and lost-reply/duplicate-delivery behavior; ordinary ACP messages, no private sibling imports or invented wire fields |
| 2: integrated experience and first component delivery | Combine the qualified boundaries for the two-stage proof below; release independently versioned artifacts and test their Unified assembly | Native plus another ACP implementation, then two participants in one conversation; installed artifacts, ownership and unsupported behavior visible |
| 3: further client and capability extraction | Publish the current client from its owning repository; introduce the supported independent compatibility/adoption boundary; continue SDK, authored component and capability packages | Select a compatible alternative and return to the tested default retaining new work; fixture and actual integration paths; AHP client migration remains separate |
| 4: catalog/read boundary and Rust comparison | Define typed catalog queries and a separately named selected-history-page interface; retain native writers; compare a bounded Rust implementation only after fixtures exist | Same inputs, visible results, error/expiry semantics and cold/warm cost measurements; return to current implementation without rewriting history |

The three first batches run in parallel. Their common milestone is independently
runnable artifacts and an actual native ACP conversation, not a complete migration.
Use existing owned repositories or local fixture packages while repository/package
registration is pending. Neither SDK publication, the exhaustive audit, generated
static removal nor a language choice gates these first batches. Keep those items,
developer feedback adoption, automatic resource/evidence lifetimes and the turnkey
release path on the backlog; this changes ordering, not completion criteria.

The first client artifact remains paired with the exact server source revision.
An export is disposable build input, not a new canonical copy of shared sources.
It preserves the current monorepo build and does not introduce compatibility
fallbacks or retire tracked static assets. The SDK's extraction must preserve the current
extension validator: it fingerprints static JavaScript assets, so narrowing that
coupling requires an explicit validator/host compatibility decision, not just a
package move. Existing bundled web/server rules continue until their public seam
and independent adoption policy are deliberately introduced.

### First experience proof

Use an owned DTU, synthetic state and Reality Check/user-journey simulation. Reuse
the current root subprocess and `WorkerRetention` mechanisms, adapting the retained
native ACP prototype rather than adding another supervisor. First prove one
conversation participant backed by native execution and a second implementation
in a separate conversation through the same boundary. Then prove two agent
participants in one experience-owned conversation, with distinct authorship and
private execution identities. An agent may also access two connected conversations
without the host dictating how its runtime organizes context or memory.

Exercise send/stream, background work with zero viewers, disconnect/reconnect,
Stop, standing Full authorization, revocation, lost reply, duplicate delivery,
idle retirement/wake and failure recovery. System-delegated work needs no new
human-presence requirement. Keep unknown effects unknown; neither replay nor a
second writer is a recovery shortcut. Shared conversation retrieval must respect
membership and preserve authorship; native private history is not automatically
public conversation content. The user experience must remain honest when a peer
lacks a capability.

Measure fixed active work while increasing historical catalog size, then vary
selected history size and concurrent workers independently. Report host and worker
CPU/RSS separately, encoding/copy volume, catalog/history reads, latency, start/wake
cost and retained resources after churn. Multiple processes enable scheduling;
they do not prove useful parallelism or remove serialization. Preserve exact
source, installed artifacts, provider and cold/warm identities under the existing
[performance policy](../validation/performance.md). Synthetic-provider results do
not establish real-provider acceptance.

Viewer loss, host-process loss, settled-worker retirement, DTU stop/start and true
suspend/resume are distinct checks. Current host shutdown stops native workers;
the proof must report that behavior rather than claim independent host survival.
Keep resources needed by accepted work, other consumers or review holds. Archive
may trigger reconciliation after those checks; it does not delete durable history.
Do not advertise DTU hibernation until its owning tool supports and qualifies it.

### Decisions before the corresponding implementation

| Decision | Why it cannot be settled by moving files |
| --- | --- |
| Conversation/participant mapping to AHP and ACP | ACP execution context is not automatically the shared conversation; qualify authorship, membership, delivery identities and unsupported-peer behavior |
| Surviving host-process replacement | Existing root workers stop with the host; use a proven independent owner or a tested drain, with recovery and exclusive execution intact |
| Resource wake and retirement | Existing tools own stop/remove/reconcile; choose supported wake semantics and shared-consumer handling before tying them to conversation archival |
| Public component compatibility and distribution | Confirm accessible artifacts, interface revision policy, installed versions and default lock; alternatives must be usable without rebuilding the ecosystem |
| Rust target and packaging | Compare the bounded reader after the interface fixture; compiler feedback and binary delivery are benefits to measure, not evidence of better architecture by themselves |

The turnkey release entrypoint remains a required follow-on tooling/workflow
deliverable. Choose its owner after the first independently releasable artifact;
it should guide an agent through existing focused checks and report exact published
and adopted versions. It is not a universal agent workflow or an extra approval
service. Unified packages a tested set; component publication and changing that
default are distinct operations.
