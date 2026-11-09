# Agent protocol — v1 (RATIFIED 2026-10-02)

Clause prefix: **AP**. Parent: [family vision](../docs/architecture/VISION.md).
Governs ACP agent adapters and the host that calls them.

## Who builds against this

Amplifier adapter/runtime maintainers, generic host implementers and independent ACP clients.

## What it is

An agent adapter wraps the native application runtime. The generic host uses ACP
for execution, rather than importing an engine's objects or storage implementation.

```text
host or independent ACP client -> ACP adapter -> native runtime and storage
conversation participation -> adapter/runtime-chosen execution binding
native execution identity -> adapter kind + native session ID + exclusive writer
optional capability -> negotiated extension or explicit unavailable result
```

[Protocol fit](../docs/architecture/protocol-fit.md) contains the mapping inventory.

ACP's session is not assumed to be the experience's shared conversation. Adapters
own that mapping and their context/memory strategy, including whether one instance
uses separate native contexts or retrieves connected conversations into a shared
working context. Standard protocol behavior remains intact; no invented ACP field
is required merely by this distinction. [HP7](host-protocol.v1.md) owns the shared
conversation boundary. External work or an event response need not produce a chat
reply; an agent's supported triggers and outcome channels belong to its implementation.

## The promises

1. **AP1 — Use one execution boundary.** New host execution reaches every agent,
   including Amplifier, through negotiated ACP. Standard capabilities precede
   extensions. Direct native access remains inside the adapter, not a production shortcut.
   Broken: an Amplifier-only host path bypasses the interop tests. Affected: replacement agents.

2. **AP2 — Preserve native composition.** The Amplifier adapter retains supported
   bundles, providers, tools, hooks, contexts and loops. Adapter extraction is not
   permission to substitute a differently constrained engine or discard capabilities.
   An explicit dependency-preparation policy must be enforced by the native owner
   before readiness and throughout its applicable execution lifetime. A readiness
   claim cannot replace enforcement or silently weaken the requested policy.
   Broken: ACP adoption silently removes a provider or live loop. Affected: Amplifier users.

3. **AP3 — Keep identity and authority.** A native execution session retains its
   agent/native identity; one execution owner writes its native history. A shared
   experience conversation can have several agent participants without merging
   their native identities or becoming owned by one of them. ACP loading/resuming and lock
   acquisition preserve that authority; UI attachment neither changes engines nor takes ownership.
   Broken: a new viewer starts a competing native writer. Affected: every session participant.

4. **AP4 — Translate lifecycle faithfully.** Prompt completion, cancellation,
   permissions, errors, tool progress and resource callbacks retain upstream meanings.
   Capabilities without a faithful mapping are explicit limitations or negotiated extensions.
   Broken: an admission response is treated as a finished turn. Affected: clients and automation.

5. **AP5 — Keep advanced behavior optional.** Standard config options expose supported
   session controls. Composition administration and advanced runtime controls have
   separate scoped interfaces; their absence never blocks an ordinary supported turn.
   Broken: a generic ACP client must implement Amplifier's settings UI. Affected: community clients.

6. **AP6 — Report legacy compatibility honestly.** Classify existing native sessions
   as qualified for resume, temporarily owned elsewhere, read-only, or unsupported
   with a reason. Preserve their records. Never recreate state by replaying past tool work.
   Broken: failed resume becomes a silent new conversation. Affected: users with historical work.

## Not in v1

Cross-engine session conversion or identical advanced features in every peer; promote
only with explicit semantics and independent acceptance. No new kernel protocol dependency.

## How the kit checks it

- AP1/AP4: run our host with another ACP agent and our agent in an independent client.
- AP2/AP5: compare every affected module-class implementation and advertised control.
- AP3: race independent clients/hosts against the same native session and inspect writes.
- AP3/HP7: connect two agents to one conversation and one agent to two conversations;
  check shared transcript ownership, authorized retrieval and distinct native writers.
- AP6: exercise legacy roots, children, missing modules and interrupted ownership.

Inspect the full control inventory before declaring native feature parity.

## Open questions

Which advanced loop controls need extensions beyond current upstream capabilities?
Which extracted native policies need separate reusable packages to keep the agent boundary small?

## Changelog

| Date | Change | Evidence |
| --- | --- | --- |
| 2026-11-09 | Require truthful enforcement of explicitly requested dependency-preparation policy. | [Native preparation boundary](../docs/architecture/protocol-fit.md#native-dependency-preparation); focused worker qualification remains separate from ACP parity. |
| 2026-11-07 | Scope exclusive execution ownership to native identity, separate from shared conversation participation. | [Conversation ownership design](../docs/architecture/state-and-storage.md#conversation-participation-and-execution); implementation qualification remains pending. |
| 2026-10-02 | Ratified for implementation; exclude amplifier-agent as the native backend. | User approval of the plan and direction; implementation evidence remains separate. |
| 2026-10-02 | Initial AP1–6. | [Native runtime and external adapter review](../docs/architecture/evidence.md). |
