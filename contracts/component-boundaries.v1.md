# Component boundaries — v1 (RATIFIED 2026-10-02)

Clause prefix: **CB**. Parent: [family vision](../docs/architecture/VISION.md).
Governs how independently owned repositories compose into Amplifier Unified.

## Who builds against this

Client, host, agent, capability and distribution maintainers; integration and release owners.

## What it is

Components have public boundaries, standalone development entrypoints and explicit
adoption evidence. Repository count does not prescribe deployment process count.

```text
component release -> public interface + fixture peer + compatibility record
distribution -> selected component releases + tested integration + adoption receipt
implementation lane -> owning repo/paths + clause + falsifier + independent check
```

[The repository map](../docs/architecture/repositories.md) proposes names and owners.
[CD](client-development.v1.md) owns evidence discipline and coordinated changes.
[Repository access](repository-access.v1.md) governs public downloads, scoped
anonymous retries and authenticated mutations across component boundaries.

## The promises

1. **CB1 — Make development independent.** Each client, host and agent adapter can
   start against a public fixture peer without another component's checkout or live
   account. Optional capability absence has a documented ordinary behavior.
   Broken: editing the TUI requires starting the web app and its private runtime. Affected: parallel teams.

2. **CB2 — Enforce dependency direction.** Connected clients depend on protocols and
   client libraries; the generic host depends on agent protocols; native internals
   stay behind agent adapters. Optional domains never become host core dependencies.
   Broken: a generic host imports an Amplifier loop or Converge-specific model. Affected: substitute implementations.

3. **CB3 — Give each rule one home.** Reference upstream protocol semantics and one
   owner for each Amplifier extension, storage format or product promise. Generated
   types may travel across repos; copied normative definitions do not.
   Broken: two packages disagree on the same permission or receipt. Affected: implementers and reviewers.

4. **CB4 — Qualify real substitutions.** Publish exact tested client/host/agent and
   capability combinations, separating fixtures, actual peers and deployed use.
   A green default stack alone never establishes an interchangeable component.
   Broken: an adapter README is reported as passing runtime compatibility. Affected: community adopters.

5. **CB5 — Keep releases composable.** Components version and release independently.
   Unified publishes an exact tested default set and absorbs installation/assembly
   prerequisites. Users can configure alternatives through public interfaces. Report
   actual selected versions, compatibility limits and how to return to the default;
   an alternative is not automatically part of its qualification. Updates follow
   qualification and safe activation, preserving prior receipts and newer user work.
   Broken: independent releases force manual source assembly, a private allowlist
   prevents substitution, or a source update is confused with a freshly loaded worker.
   Affected: operators and component developers.

6. **CB6 — Isolate implementation lanes.** Each lane names its writable repo/paths,
   public producer/consumer seam, dependencies and falsifiable acceptance before
   integration. Independent work uses owned checkouts, state roots and service ports.
   Broken: agents overwrite shared contracts or restart another lane's service. Affected: parallel contributors.

## Not in v1

Mandatory microservices or a new central runtime framework; promote only when a
measured isolation or deployment requirement warrants the additional boundary.

## How the kit checks it

- CB1/CB2: boot each component with fixture peers; enforce dependency/import checks.
- CB3: resolve contract references and generated-schema provenance.
- CB4: execute both directions of external interoperability qualification.
- CB5: install the packaged default without sibling checkouts, substitute a component
  through its public interface, record actual package/process/worker versions, and
  return to the default while preserving newer data and uncertain effects.
- CB6: review the lane brief and rerun its seam test outside the implementing agent.

## Open questions

Which optional capabilities should be extracted in the first wave versus after the core seam?
Which existing packages already own a suitable implementation, avoiding duplicate services?

## Changelog

| Date | Change | Evidence |
| --- | --- | --- |
| 2026-11-07 | Clarify independent component releases, tested default composition and configurable alternatives. | Product-owner direction; [repository map](../docs/architecture/repositories.md). Existing bundled compatibility remains until a boundary is deliberately extracted. |
| 2026-10-02 | Ratified for implementation; exclude amplifier-agent as the native backend. | User approval of the plan and direction; implementation evidence remains separate. |
| 2026-10-02 | Initial CB1–6. | [Refreshed Converge references](../docs/architecture/evidence.md). |
