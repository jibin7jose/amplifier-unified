# Source assessment and evidence boundaries

The October 2 assessment and synthetic probe below retain their original source
and measurement identities. The separate November 7 review updates ownership and
extraction choices; neither review is current runtime acceptance of the proposal.

## November 7 ownership review

Reviewed main at
[`9720e3ee`](https://github.com/microsoft/amplifier-unified/tree/9720e3ee52f210fbba3297c28d2e562e12671435).
This was targeted source inspection across native execution, history/catalog,
client/component seams and the earlier candidate proposal. It is not a completed
whole-codebase audit, a benchmark rerun or a new interoperability qualification.

| Finding / exact source | Consequence for the next batch |
| --- | --- |
| [`RuntimeManager`](https://github.com/microsoft/amplifier-unified/blob/9720e3ee52f210fbba3297c28d2e562e12671435/amplifier_web/runtime.py#L466) already starts native root workers; [`WorkerRetention`](https://github.com/microsoft/amplifier-unified/blob/9720e3ee52f210fbba3297c28d2e562e12671435/amplifier_web/runtime_retention.py) owns idle policy | Reuse process/lifecycle owners; extracting a repo does not require another supervisor |
| [`Children.spawn`](https://github.com/microsoft/amplifier-unified/blob/9720e3ee52f210fbba3297c28d2e562e12671435/amplifier_web/host/children.py#L416) executes delegated sessions within the root worker; [`RuntimeManager.close`](https://github.com/microsoft/amplifier-unified/blob/9720e3ee52f210fbba3297c28d2e562e12671435/amplifier_web/runtime.py#L1709) stops workers | Neither one process per every native child nor survival of host shutdown is current behavior |
| [`SessionStore`](https://github.com/microsoft/amplifier-unified/blob/9720e3ee52f210fbba3297c28d2e562e12671435/amplifier_web/host/storage.py) wraps Foundation history; [`runtime_worker.py`](https://github.com/microsoft/amplifier-unified/blob/9720e3ee52f210fbba3297c28d2e562e12671435/amplifier_web/runtime_worker.py#L874) acquires native writer ownership | Preserve native continuation and exclusive writer identity when separating experience conversations from agent participation |
| [`HistoryDirectory`](https://github.com/microsoft/amplifier-unified/blob/9720e3ee52f210fbba3297c28d2e562e12671435/amplifier_web/history_directory.py#L781) already selects bounded SQL pages; [`TranscriptWindowReader`](https://github.com/microsoft/amplifier-unified/blob/9720e3ee52f210fbba3297c28d2e562e12671435/amplifier_web/transcript_window.py#L110) has a selected-history read boundary | Preserve these distinct readers; do not claim dormant discovery requires every agent to run or replace native writers with an index |
| [`shell-sdk`](https://github.com/microsoft/amplifier-unified/tree/9720e3ee52f210fbba3297c28d2e562e12671435/packages/shell-sdk) already exports a package API | First extraction can publish the existing interface with standalone authoring fixtures, before a runtime or transport migration |

The retained [PR448 distribution manifest](https://github.com/microsoft/amplifier-unified/blob/20f6bb078617f4d85d2f11932d103a69c8577fa2/distribution/components.json)
and its packaged host/native integration artifacts demonstrate prior implementation
work, not just proposed repository names. Selected artifact inspection found ACP
process ownership, resume/load handling and retirement behavior worth reusing.
The prototype predates current main's history readers and lifecycle fixes; compare
and adapt those changes before claiming parity. Public availability and independent
installation of every referenced dependency still need qualification.

Constellation research was retained at exact sources:
[umbrella `d0347431`](https://github.com/microsoft/amplifier-constellation/tree/d03474310e74f0b5f7b2de1b1ca4c895783c0e9b),
[Runtime `1ce2aede`](https://github.com/microsoft/amplifier-constellation-runtime/tree/1ce2aede54a16ed2377e9073ec5ec1656d624bc6),
[Instance `6fe76d67`](https://github.com/microsoft/amplifier-constellation-instance/tree/6fe76d6738f4c10debdcbc554815eff022b71868).
Its experience/connector/runtime separation informs the proposal. It does not
establish native Unified compatibility: connection lifetime and runtime memory
remain implementation-specific, and Rust alone does not remove full selected-history
loading/serialization. Reuse appropriate interfaces without importing a mandatory
scheduler, memory layout or agent workflow.

The [candidate README](../candidates/modular-e2e-architecture/README.md#relationship-to-current-direction)
identifies superseded assumptions while preserving its original source audit and
diagrams. The [next batches](delivery-plan.md#next-bounded-batches) identify the
runtime, packaging and experience checks that remain to be performed.

## Unified baseline

Source: [Unified e9a3a2fa / v0.20.45](https://github.com/microsoft/amplifier-unified/tree/e9a3a2fa9a6f2291e451fb95a0202b0a0b9156e6).
The docs branch starts from that revision. Source and fixtures were inspected;
the production and development hosts were not modified or measured for this packet.

During document preparation, main advanced to
[828d2bff](https://github.com/microsoft/amplifier-unified/commit/828d2bff97a77fbdbd0f06429369f7ff1f4abf42)
for tool-error/conversation-outcome presentation. The changed-file comparison did
not change the save/publication, agent-query, retention or discovery files below.
The recorded timings remain the earlier exact source, not a rerun of that commit.

| Finding | Source at the reviewed revision | Implication |
| --- | --- | --- |
| Scoped save/publication already exists; unknown/global paths still choose broad save and projection work | [`service.py`](https://github.com/microsoft/amplifier-unified/blob/e9a3a2fa9a6f2291e451fb95a0202b0a0b9156e6/amplifier_web/service.py#L656), `_save` and `_publish` | Preserve proven narrow paths; protocol replacement alone will not remove broad fallbacks |
| Narrow durable records overlay a broader checkpoint | [`state_records.py`](https://github.com/microsoft/amplifier-unified/blob/e9a3a2fa9a6f2291e451fb95a0202b0a0b9156e6/amplifier_web/state_records.py) | Scoped durable entities must become the normal unit rather than exceptions around a global object |
| Browser projections and delta transport are present | [`state_projections.py`](https://github.com/microsoft/amplifier-unified/blob/e9a3a2fa9a6f2291e451fb95a0202b0a0b9156e6/amplifier_web/state_projections.py), [`state_transport.py`](https://github.com/microsoft/amplifier-unified/blob/e9a3a2fa9a6f2291e451fb95a0202b0a0b9156e6/amplifier_web/state_transport.py) | Small wire payloads do not prove small projection/diff cost; shell keys still traverse broad catalog facts |
| Agent state query constructs a state context before extracting a requested pointer | [`agent_canvas.py:92`](https://github.com/microsoft/amplifier-unified/blob/e9a3a2fa9a6f2291e451fb95a0202b0a0b9156e6/amplifier_web/agent_canvas.py#L92), [`agent_state.py`](https://github.com/microsoft/amplifier-unified/blob/e9a3a2fa9a6f2291e451fb95a0202b0a0b9156e6/amplifier_web/agent_state.py) | Even a scalar response can incur broad work; replace with direct scoped reads |
| Private client views/drafts remain backend-managed | [`client_views.py`](https://github.com/microsoft/amplifier-unified/blob/e9a3a2fa9a6f2291e451fb95a0202b0a0b9156e6/amplifier_web/client_views.py) | Move ordinary private persistence to clients, retaining explicit shared/agent-visible capabilities |
| Runtime retention exists: defaults include 32 warm workers, 12-hour idle threshold and prewarm on selection | [`runtime_retention.py`](https://github.com/microsoft/amplifier-unified/blob/e9a3a2fa9a6f2291e451fb95a0202b0a0b9156e6/amplifier_web/runtime_retention.py#L8) | Reconsider what deserves runtime residency; count limits alone do not bound bytes |
| Display cooling and automatic history/watch mechanisms exist | [`cold_display.py`](https://github.com/microsoft/amplifier-unified/blob/e9a3a2fa9a6f2291e451fb95a0202b0a0b9156e6/amplifier_web/cold_display.py), [`automatic_history.py`](https://github.com/microsoft/amplifier-unified/blob/e9a3a2fa9a6f2291e451fb95a0202b0a0b9156e6/amplifier_web/automatic_history.py), [`history_watch.py`](https://github.com/microsoft/amplifier-unified/blob/e9a3a2fa9a6f2291e451fb95a0202b0a0b9156e6/amplifier_web/history_watch.py) | Extend existing useful work into indexed discovery and separately budgeted history views |

## Synthetic cost-shape probe

Recorded [measurements](evidence/measurements.json) and
[probe source](evidence/baseline-probe.py). Real AppService/projection/persistence
code, synthetic catalog and tiny message bodies, no model calls. Three sequential
samples per operation; table shows medians in milliseconds. No HTTP/SSE encoding,
browser painting, production filesystem, active-model contention or RSS measurement.

| Stored chats / attached clients | Scoped detail publication | Global publication | Private draft action | Agent scalar `/revision` read |
| --- | ---: | ---: | ---: | ---: |
| 100 / 1 | 12.33 | 6.16 | 3.96 | 1.13 |
| 5,000 / 1 | 7.97 | 30.78 | 34.22 | 34.41 |
| 22,500 / 1 | 12.46 | 137.51 | 156.68 | 214.91 |
| 22,500 / 8 | 10.77 | 161.36 | 182.28 | 225.57 |

This supports the hypothesis that unrelated historical scale still affects some
small operations; it does **not** identify the full cause of multi-second UI
latency. The fixture varies workspace count as well as session count, so it does
not isolate their separate effects. The delivery plan requires an end-to-end
trace and independent count variation before claiming a performance improvement.

The probe used current source with an existing `release-02031-charlie` dependency
environment, not a freshly resolved v0.20.45 environment. An initial environment
attempt lacked `amplifier_foundation.settings`; those failed-start attempts are
not measurements. Rerun using the exact source, its test fixtures and a compatible
dependency environment; record a new result rather than overwriting this receipt.
The script is an investigation artifact, not an added production regression test.

## Refreshed source context

[The manifest](evidence/upstream-revisions.json) records full revisions for the
Converge umbrella and related repos, protocols, native runtime libraries, TUI/CLI,
daemon and adapter sources. Source snapshots were refreshed as read-only reference;
none were installed or treated as proof of running behavior.

The current [Converge checkpoint](https://github.com/microsoft/amplifier-converge/blob/c63f1e6bea6d93e5ffb86b0e3dbcc698391cbe9c/docs/workflow/UNIFIED-CHECKPOINT-20260923.md)
keeps Method, portable operating guidance, optional Unified integration, domain
capabilities and native execution as separate owners. It explicitly does not
establish Codex/Claude/Copilot compatibility. This design carries that distinction
forward and uses bounded lane briefs, public seam tests and independent checking.
No changes were made to Converge's own ratified/locked documents.

The refreshed `amplifier-agent` contracts differ materially from its description
in the ecosystem module catalog: it now has embedding APIs and a frozen engine
contract, including a one-provider constraint. Use the repository's actual
contract for evaluation, not the catalog's older per-turn-subprocess description.
The TUI is already a separate repository and its connected mode has local state
and an HTTP backend; extraction work should evolve that seam rather than start over.

Before changing an Amplifier module class, the implementation lane must inventory
**every known affected implementation**: current `docs/MODULES.md`, configured,
local, embedded and community sources, exact revisions, unaffected cases and
coverage gaps. This packet is an architecture assessment, not a claim that a
class-wide implementation audit or full runtime parity qualification is complete.

## Protocol references that control design choices

- [AHP/ACP relationship](https://microsoft.github.io/agent-host-protocol/guide/ahp-and-acp.html)
  and [state model](https://microsoft.github.io/agent-host-protocol/guide/state-model.html).
- [AHP subscriptions](https://microsoft.github.io/agent-host-protocol/specification/subscriptions.html),
  [reconciliation](https://microsoft.github.io/agent-host-protocol/guide/reconciliation.html),
  [root catalog](https://microsoft.github.io/agent-host-protocol/specification/root-channel.html)
  and [chat history/drafts](https://microsoft.github.io/agent-host-protocol/specification/chat-channel.html).
- [ACP v1 session setup](https://agentclientprotocol.com/protocol/v1/session-setup),
  [prompt lifecycle](https://agentclientprotocol.com/protocol/v1/prompt-turn),
  [configuration](https://agentclientprotocol.com/protocol/v1/session-config-options)
  and [extensions](https://agentclientprotocol.com/protocol/v1/extensibility).
- [AHP implementations](https://microsoft.github.io/agent-host-protocol/guide/implementations.html)
  and [ACP clients](https://agentclientprotocol.com/get-started/clients), supplemented
  by the actual adapter references in [protocol fit](protocol-fit.md).

## What remains unverified

Actual AHP/ACP peer interoperability, legacy native resume through the new adapter,
end-to-end latency, memory budgets, feature parity, physical-device behavior,
real-account authentication/model calls, released components and deployed worker
adoption. Those are explicit delivery gates, not conclusions of this document.
