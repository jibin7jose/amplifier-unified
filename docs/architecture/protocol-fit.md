# Protocol fit, capabilities and interoperability

Design supporting [HP](../../contracts/host-protocol.v1.md) and
[AP](../../contracts/agent-protocol.v1.md). Sources were refreshed on 2026-10-02;
exact inspected commits are recorded in [evidence](evidence.md). These are
source/documentation findings, not live interoperability results.

## Prefer upstream semantics

The [AHP/ACP guide](https://microsoft.github.io/agent-host-protocol/guide/ahp-and-acp.html)
describes complementary roles: a durable host mediates clients using AHP and can
reach agents using ACP. Follow the owning specification when guide examples or
our design notes differ. Do not copy the standard into an Amplifier wire document.

At the reviewed revisions, AHP's current protocol version is `0.9.0` and ACP's
stable wire protocol is `1`. ACP's v2 directory is not evidence that v2 is stable.
Package versions and schema releases are distinct from negotiated wire versions.
Qualification records must name SDK/schema, host, agent, client and capabilities.
Track upstream releases through compatibility tests; inspection SHAs are receipts,
not standing deployment pins. Recheck versions before implementation and release.

Use AHP's root/session/chat/resource surfaces, upstream reducers, subscriptions,
reconciliation and supported transport. Prefer the official TS/Rust tooling when
it fits the extracted components. Evaluate host language/runtime with a small
conformance spike; a protocol decision does not require rewriting the Python
agent runtime. ACP stdio is the initial process boundary. Do not assume proposed
remote transports or a vendor's TCP mode are a portable multi-client solution.

## Wrap the native application runtime

Yes: ACP can be the **sole normal host-to-agent execution interface**. Wrap the
runtime layer that prepares bundles and owns sessions, hooks, persistence,
approvals, cancellation and native ownership. Merely translating
`AmplifierSession.execute(str) -> str` misses streamed tools, questions, images,
runtime controls and lifecycle. Keep the kernel and modules protocol-independent.

The generic host starts/configures an agent process, negotiates ACP, admits work,
maps updates to AHP and holds the ACP connection independently of UI lifetime.
The adapter translates native events incrementally rather than exporting the
entire native context. Persist native-ID mappings and ownership with a clear
crash-recovery rule; a process pool is an optional optimization after isolation
tests, not one process per historical session.

The November 7 [ownership clarification](state-and-storage.md#conversation-participation-and-execution)
distinguishes the shared conversation from the agent's execution context. ACP
[`session/new` and optional load](https://agentclientprotocol.com/protocol/v1/session-setup)
establish an agent context; they do not assign ownership of an experience's
multi-participant transcript. A connector/adapter maps participation to the
runtime's chosen contexts. Qualify the actual AHP/ACP representation, authorship,
retrieval and unsupported-peer behavior before implementing it; this design adds
no private fields to the standard by implication.

**Do not equate two existing products.** The freshly reviewed `amplifier-agent`
repository now offers Python/TypeScript embedding APIs and a frozen engine contract.
That contract constrains one provider per agent and its own configuration surface.
It is not established as a drop-in replacement for today's bundle/module runtime.
Build `amplifier-agent-acp` over the runtime we already use. The user explicitly
excluded `microsoft/amplifier-agent` from the migration; do not adopt it as the backend.

### Native dependency preparation

An explicitly immutable native worker binds Foundation's public dependency-installation
policy before accepting work. That policy covers inherited asynchronous/thread work,
lazy activation and joined cleanup for the worker lifetime. Only after binding the
compatible policy may its native readiness report include `runtimePreparation`:
`version: 1`, `immutable: true`, `dependencyInstallation: false`, and
`policy: "foundation-dependency-installation"`. Missing policy support or a malformed
launcher value fails before readiness; ordinary mutable startup retains its behavior.

This is the native launcher/worker boundary, not an added ACP field. The adapter
checks the report; it does not manufacture it. The policy prevents Foundation
dependency installation, not arbitrary module filesystem or subprocess effects.
It establishes neither Full authorization nor source closure, cancellation, resume
or broader protocol conformance; those need their own evidence.

### First native execution-owner integration

The next bounded pilot injects a trusted execution owner into the existing ACP
server. Standard clients send ordinary ACP requests; the native owner supplies
Unified's authority and lifecycle behavior internally. Reuse the existing parser
and standard update projection rather than implementing a second wire translator
or pretending an owner is a worker process.

The initial owner delegates the complete lifecycle to existing `AppService` and
`RuntimeManager` paths. Those paths create native identities, admit work, correlate
bridge acknowledgments/cancellation, ingest usage and settle durable receipts.
Routing only `capacity.admit` would omit the latter obligations. No adapter-owned
accounting store or synthesized generation is part of this integration.

| Owner operation | Meaning |
| --- | --- |
| Create | Return the identity from the canonical session-creation path. |
| Attach saved conversation | Resolve the exact saved root identity in the launcher's trusted history workspace without loading history or starting execution. |
| Prompt | Submit one identified input and observe its matching native outcome; acceptance alone is not completion. |
| Cancel | Request the existing Stop operation; report the actual terminal outcome, including completion races. |
| Release observation | Detach this observer without archiving history or cancelling accepted work. |
| Close owned service | Use the existing owner shutdown/settlement policy; preserve durable work and do not promise survival of owner loss. |

The trusted launcher supplies the authority binding. ACP prompt data cannot grant Full
or substitute an execution owner. Targeted public updates follow the existing
owner's event handling; they do not expose unrelated sessions or private model
context, and are not themselves durable admission receipts. Standing Full, live
write-policy changes, child ceilings, Stop and reservation/usage ownership retain
their current semantics. Missing load, configuration, MCP or extension support in
this experimental path stays unadvertised and fails explicitly without creating
a replacement session. The existing adapter path keeps its supported behavior.

The next slice advertises optional standard `session/resume`, which returns no
history replay. Requested `cwd` must match the owner's saved execution location
(`workingDirectory`, falling back to `workspace`); it cannot move the conversation
or broaden its permissions. Metadata attachment does not acquire an exclusive
writer or establish that native execution can resume. The next prompt uses the
existing owner admission, lock, configuration and native-history checks, reporting
busy, read-only or unsupported outcomes without creating a replacement or replaying
an earlier input. Root identity and saved history survive a fresh owned service.
Full `session/load` remains unadvertised: ACP requires complete visible-history
replay, which a bounded recent-message page cannot satisfy. Existing passive
history-window reads remain with their current owner. Consumers supporting only
load, including the currently reviewed Conversations connector, are not yet
qualified for reconnect continuity through this path.

Interactive approval forwarding is a separate limitation of this first path.
The existing access-mode owner resolves standing Full; the adapter may join its
actual settlement without becoming another policy evaluator. A genuinely pending
request with no supported client surface produces an explicit unavailable result
and detaches observation, preserving the canonical request and accepted work.
It cannot silently wait, manufacture a decision, cancel work or authorize replay.
User-question interaction also needs separate qualification.

The trusted launcher may opt into standard stdio and HTTP MCP attachments when
its selected bundle contains the qualified canonical `tool-mcp` module. ACP
session setup remains metadata-only; the first prompt prepares the worker and
requires confirmation of every requested server before admitting model work.
The native host supplies `mcp.ephemeral_servers` through `before_initialize`,
separately from the mount plan. The module uses its one manager, preserves
configured servers, consumes the temporary input and confirms only installed
names through `mcp.ephemeral_ready`. Missing or older module support fails
explicitly; the adapter neither installs another manager nor invents readiness.

These descriptors belong to the trusted attachment and native worker lifetime.
They travel through private worker IPC and survive that worker's remount, but do
not enter durable session/configuration plans, retirement snapshots or history.
Observation release does not stop admitted work or dispose tools it still uses;
new input or direct tool use cannot borrow that released attachment's live
credentials. Passive inspection and existing Stop/reply paths remain available.
Rebinding a live worker is refused without replacing or cancelling it. A fresh
owner must supply its own connection descriptors rather than restoring secrets
from saved history. Worker/coordinator shutdown owns transport cleanup.

This path retains standing Full and existing tool/source authorization. Enabling
MCP is a trusted launcher configuration choice, not an ACP prompt assertion or a
new per-call approval. Source membership and Instance leases remain authoritative
for future reads; revocation does not erase previously authorized context.
Qualification distinguishes an Instance owner-authorized lease from an opaque
current-room grant, and does not claim arbitrary third-party logging is secret-free.

This is an experimental in-process boundary around a composite legacy owner,
not yet a remote authority protocol, a lightweight broker or a new shared
conversation implementation. Its first evidence must distinguish a failed
reservation commit from a lost acknowledgment after commit, preserve unknown
effects without replay, prevent dispatch after Stop/revocation, and exercise an
actual protected action under standing Full without a new human callback.

## Mapping inventory

This assigns a destination, not a promise that every mapping already exists.
The agent lane must expand it to the complete affected module/control inventory.

| Current behavior | Preferred boundary | Constraint / evidence required |
| --- | --- | --- |
| Initialize, authenticate, new session, prompt, streaming output/tool progress | Standard ACP; translated to standard AHP chat lifecycle | Preserve negotiated content types, failures and permissions |
| Native load/resume | Standard optional ACP capabilities | `session/load` replays visible history; `session/resume` avoids replay when supported; do not invoke load just to list/view |
| Turn cancellation | Standard ACP `session/cancel` | Cancellation is not rollback, force process death or live steering |
| Session close/worker retirement | Optional ACP close plus native safe-lifecycle policy | Close may cancel/free resources; never equate it with a client unsubscribe |
| Modes, model and simple supported session choices | ACP `configOptions`, with legacy modes when required | Standard response/update contains the complete config-options list, not a private partial patch |
| Permissions and agent questions | Standard ACP permission/content surfaces where faithful; AHP input surface | Arbitrary rich forms may need extensions; one accepted answer across viewers |
| Filesystem and terminal callbacks | Host as ACP client, scoped to execution workspace | Never route authority implicitly to whichever browser last sent input |
| Native parent/child activity | Standard tool reporting baseline; negotiated subagent/session extensions when available | Do not assume draft subagent proposals are universally implemented; child histories load on demand |
| Queue, live steer, goals/budgets, autonomous continuation | AHP controls backed by a faithful ACP capability/extension | Do not silently emulate steer by cancel-and-reprompt or infer goals from plain prompts |
| `operations.submit/cancel/write`, force stop and takeover | Standard lifecycle where exact; adapter/runtime control extension otherwise | Inventory cancellation/ownership fences and agent-process failure separately |
| `native.status/compact`, `context.clear`, `memory.consolidate`, `goals.*`, `budget.*` | Native agent control extension or supported standard command | Preserve user/agent action parity; declare unsupported peers |
| `provider.select`, `mode.*` | Standard session config when representable | Complete option state and clear apply point; advanced routing stays composition config |
| `provider.reset`, `tool.invoke`, native diagnostics | Scoped native control/admin capability | No generic host dependence on Amplifier internal method names |
| `configuration.inspect/apply/toggle/exportResources` | Host/agent configuration administration with standard session selectors where suitable | Persisted/effective/mounted distinction; bundle/module composition remains modular |
| `configuration.providers/providerModels/providerTest/providerLogin`, `catalog.inspect`, `usage.inspect` | Dedicated catalog/auth/config/read capabilities, selected standard fields where available | Listing never silently probes, logs in or mounts every provider |
| `history.snapshot`, native transcript/export | Native store reader behind adapter/resource interface | Viewing must not prepare execution or trigger ACP full-history load unnecessarily |
| Artifacts, diffs, resources, terminal output | Standard ACP content/tool data and AHP resources/changesets where expressible | Stable references, capability fallback, on-demand large bodies |
| Canvas/MCP Apps, device actions, rich editors | Optional host capabilities and client renderers | Explicit audience/permissions; generic textual/artifact fallback |
| Realtime voice/video | Separate media transport with protocol-visible lifecycle/control | ACP audio content is not a full-duplex WebRTC contract |
| Schedules, background operations, Converge workflows | Host/capability owners, standard AHP surfaces when qualified | No Converge domain model in generic host; automation maturity checked separately |
| Agent inspection/actions in the application | Scoped public host actions via an MCP/tool facade | Same authoritative operation as UI; no hidden whole-app snapshot or Python callback graph |

Use upstream [ACP configuration](https://agentclientprotocol.com/protocol/v1/session-config-options)
before adding a settings extension. ACP v1 `session/prompt` completes with the
turn's outcome; an early host admission receipt is a separate fact. Do not import
v2 prompt lifecycle semantics into a v1 connection.

Every candidate extension needs a named owner, version/capability negotiation,
permission scope, producer, consumers, rejection behavior, ordinary fallback and
conformance fixture. Use ACP's `_` methods and namespaced `_meta`, and AHP's `x-`
extension conventions as specified. Prefer an existing upstream capability when
it graduates; don't require another product's opt-in extension for baseline ACP.
Extensions may carry advanced control without restoring a second generic native
execution API. Administration configures an adapter; it does not run turns around ACP.

## Important performance limits of the standards

AHP has paged `listSessions`; its root state does not contain the session catalog.
Catalog notifications are ephemeral and must be reconciled after reconnect.
Its optional shared draft need not receive every keystroke. Those choices support
the proposed local-state and bounded-discovery design directly.

There are limits to honor. AHP `view.turns` is advisory, omitting it requests all
retained turns, and `fetchTurns` grows reduced state. ACP `session/load` replays
visible history when supported; resume without replay is an optional capability.
We cannot promise strict bounded memory for arbitrary peer behavior without
testing these paths and settling any retention/extension gaps. See
[state design](state-and-storage.md) for the safeguards and open boundary.

Neither protocol guarantees exactly-once external tool effects. The host keeps
durable admission receipts; downstream uncertainty remains unknown unless native
identity/history proves the result. Never resend prompts merely because a socket
closed. Host event replay must only restore a view, not repeat execution.

## Existing external implementations to qualify

| Candidate | Confirmed source/documentation | Proposed test; current result |
| --- | --- | --- |
| GitHub Copilot CLI agent | [Native ACP server](https://docs.github.com/en/copilot/reference/copilot-cli-reference/acp-server), `copilot --acp --stdio`, public preview | Run through our host; config, permissions, cancel, resume and two AHP viewers. Runtime untested. |
| OpenAI Codex agent | [`agentclientprotocol/codex-acp`](https://github.com/agentclientprotocol/codex-acp), new adapter over Codex App Server; old Zed repo points here | Reuse adapter; validate native auth, tools, cancellation, history, negotiated extras. Runtime untested. |
| Anthropic agent | [`agentclientprotocol/claude-agent-acp`](https://github.com/agentclientprotocol/claude-agent-acp), adapter using Claude Agent SDK | Reuse adapter; validate approvals, tools, history, child fallback. Runtime untested. |
| Independent ACP client | [Zed external agents](https://zed.dev/docs/ai/external-agents) and [ACP clients directory](https://agentclientprotocol.com/get-started/clients) | Run our Amplifier ACP agent without Unified; basic and optional capability behavior. Runtime untested. |
| Independent AHP peers | [AHP implementations](https://microsoft.github.io/agent-host-protocol/guide/implementations.html), including reference SDKs and community clients/hosts | Choose a runnable client against our host and host against our client at declared versions. Runtime untested. |
| Additional engines | Choose a distinct implementation such as Gemini CLI or an ACP reference fixture after source review | Adds diversity after first three; not yet source-qualified in this packet. |

The current Codex adapter is now `@agentclientprotocol/codex-acp`; do not build on
the superseded Zed adapter based on an old README. Its current source advertises
standard features plus opt-in AIR extensions. Claude's adapter likewise has
optional extensions. These are useful compatibility evidence to examine, not
automatic requirements for Amplifier or proof of upstream standardization.

Distinguish **agent/harness integration** from **vendor client integration**.
These findings do not show that stock Codex desktop, Claude Code's own interface
or every Copilot UI can attach to an arbitrary AHP host. Claim compatibility only
for the actual executable, version, direction and feature set tested. No accounts
were authenticated and no paid model calls were run for this design packet.

An ACP-only editor has two possible paths: use our ACP agent directly for standalone
work, or use a future **ACP-facing bridge that acts as an AHP client** to our host.
The latter can preserve the host's multi-client ownership without requiring the
editor to implement AHP. Evaluate existing bridges first. Qualify session identity,
load replay, concurrent approvals and capability loss before promising it; the
bridge must not own a second history or execution runtime. This is a follow-on
compatibility lane, separate from the core host-to-agent ACP adapter.

## What we give up and gain

| Tradeoff | Consequence |
| --- | --- |
| No direct host access to live Amplifier objects | Explicit async control/events and serialization; simpler isolation, mocking and independent replacement |
| No assumption every peer has all Amplifier features | Advanced controls become negotiated; baseline clients remain useful and honest |
| Engine-bound native executions | No transparent conversion of native history to another engine; a conversation can add another agent participant without converting existing execution history |
| Local drafts by default | No implicit cross-device private-draft recovery; zero routine sync cost and independent editors |
| Cold histories and idle agents | First history page or execution start may incur latency; bounded steady-state memory and predictable navigation |
| More repositories and version combinations | More release/CI integration work; smaller ownership surfaces and parallel development against fixtures |
| Upstream-driven wire evolution | Ongoing adapter qualification and occasional extension work; community peers can challenge our assumptions |

The recommended compromise is ACP for all normal execution, with optional
protocol extensions and scoped administration for Amplifier's differentiators.
Reducing Amplifier to the smallest intersection of all peers is unnecessary.
