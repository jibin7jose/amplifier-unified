# Unified image-capability host — v1 (DRAFT)

Clause prefix: **IUNI**. Owner: `amplifier-unified`.
Proposed obligations, not ratification or permission for paid calls. The source
candidate implements the boundaries described below; completion still requires
exact-candidate checks, accepted dependencies and independent review.
Subordinate to the unchanged [family vision](../docs/architecture/VISION.md) and
[component boundaries](component-boundaries.v1.md); no Core changes are required.

## Who builds against this
Unified host/UI and imagegen/routing maintainers qualifying cross-host adoption.

## What it is

Unified owns ordinary connections, composition, enforcement and presentation;
Imagegen owns lifecycle/schema; routing owns selection; providers own native transport.
Canonical **proposed adoption dependencies** below are not delivered support;
their publication, accepted versions and installed implementations require qualification:

- [Image lifecycle](https://github.com/microsoft/amplifier-bundle-imagegen/blob/main/contracts/image-capability.v1.md).
- [Provider image operation](https://github.com/microsoft/amplifier-bundle-imagegen/blob/main/contracts/provider-image-operation.v1.md).

Synthetic boundary example, not a settings or provider-wire schema:

```text
ordinary account-blue + shared image behavior + generic routing -> admitted cover-01
imagegen receipt + verified workspace PNG -> originating-turn card + saved output
reload -> same saved output; no new generation
```

## The promises

1. **IUNI1 — Compose through the existing boundaries.** The Amplifier-facing layer
   composes the shared behavior and generic resolver without replacing the chosen root,
   instructions or chat model. Preserve explicit tool configuration and behavior opt-outs.
   Use exact mounted ordinary provider instances, not image-only credentials or host
   vendor rankings. Discovery never generates; absent support leaves chat usable.
   Image unavailability stays visible; native internals are not generic-host dependencies.

2. **IUNI2 — Qualify the replacement before shipping it.** This source candidate
   removes `image_defaults.py` / `image_setup.py` selection, the image settings menus
   and `providers.configureImages`. Shipment requires execution/migration acceptance
   in both hosts. Use generic scoped routing
   overrides/diagnostics, not replacement image settings. Version the non-destructive
   migration; retain source, scope, provenance, result and rollback evidence. Reruns cannot broaden access.

3. **IUNI3 — Preserve intent, including uncertainty.** Retain global image-off,
   behavior disable/removal, `allow_paid: false`, genuine provider denials and explicit
   account/model pins under effective scoped inheritance. Translate pins to generic overrides
   or bounded compatibility; unsupported pins refuse. Old `enabled: false` can mean
   deselection, not a ban: reconcile provenance. Ambiguity stays visible and cannot authorize
   a new account; migration cannot discard pins or silently add a paid model choice.

4. **IUNI4 — Keep enforcement and accounting on the call path.** Forward optional
   provider methods without inventing support; retain host permission, applicable budget,
   cancellation and provider-call telemetry guards. Carry effective root/child file limits;
   denial precedes dispatch, never fallback. Count image/controller usage once each;
   unknown usage/cost is not zero; absent spend enforcement is disclosed, not an enforced cap.

5. **IUNI5 — Keep delivery verifiable.** Retain originating-turn progress/result cards,
   immutable saved outputs, Canvas, downloads and pixel inspection. Independently check
   file scope, bytes, dimensions and hashes before saving; preserve inputs and exact
   parent linkage. Display observed status, not invented progress, account verification
   or pixel inspection. Reopen/download preserves the image bytes identified by the receipt;
   a broken artifact is a visible delivery failure, never a regeneration trigger.

6. **IUNI6 — Preserve mounted and saved work.** Fresh roots and children adopt only
   their effective composed policy; child changes cannot widen inherited restrictions
   or mutate a parent/sibling. Existing snapshots and loaded sessions retain their plans
   until an explicit normal refresh. Reconnect/status reads reconcile durable receipts;
   duplicate or unknown operations never replay automatically or switch accounts.

## Not in v1

Draft status applies to this contract's ratification, not to the source delta.
The candidate removes the image-only code and controls named in IUNI2. Existing
saved settings are not deleted automatically: fresh-plan translation preserves
their restrictions and pins, while explicit scoped reconciliation can remove only
a confirmed retained Off preference. Rankings, transport and schemas remain
externally owned. New host selection, dropped historical intent or changed ratified
boundaries needs a proposal; progressive streaming needs lifecycle/UI qualification.

## How the kit checks it

**Acceptance matrix.** Use isolated installed-host fixtures and a real browser;
record exact versions. Live checks need an explicit account/bounded spend; fixtures
do not establish entitlement or pixel review.

| Promise | Positive assertion | Negative assertion |
| --- | --- | --- |
| IUNI1 | Fresh supported roots share one image tool and generic resolver with distinct same-module accounts. | No resolver/capability yields visible unavailability, not hidden policy, new credentials, root replacement or lost opt-outs. |
| IUNI2 | A versioned migration retains before/after/rollback evidence; qualified fresh profiles need zero image-only settings. | Repeated migration neither deletes source state nor broadens permissions; menus are not retired before both-host evidence and no new image selector replaces them. |
| IUNI3 | Global/project/local fixtures preserve effective denials, paid opt-outs and account/model pins with provenance. | Deselection is not labelled a proven ban; ambiguous state cannot enable a new account; conflicting scopes, unsupported pins and genuine denials cannot silently fall back. |
| IUNI4 | Synthetic execution traverses instrumented guards and records known image/controller usage once each. | Denied file/tool/paid access or exhausted applicable budget produces zero dispatches; unknown usage and absent enforcement remain explicit. |
| IUNI5 | Generate/edit cards, parent hashes, saved outputs, reload and downloads identify the exact retained PNGs. | Bad hashes/dimensions, forbidden paths, wrong origin/parent or incomplete receipts cannot become saved completed output; receipt-only evidence is not pixel inspection. |
| IUNI6 | Fresh child isolation and restart fixtures retain scoped policy and old snapshot plans. | Mount/migration cannot inject into old snapshots; duplicate/unknown requests and reconnect cause zero new dispatches or account switches. |

The current integration is based on accepted Unified `05f4bcc7`. It preserves
that source's decision-account projection, child-run admission, scoped settings
drafts, dependency lock and release metadata. It adds maintained regressions
for image-call run identity, saved image pins alongside decision accounts,
credential-marker normalization and explicit capability reconciliation.
Those changed-flow checks and the canonical frontend build remain unrun in
this source checkpoint. Prior synthetic and installed-worker evidence applies
only to its recorded inputs, not this integrated tree or paid provider use.

## Open questions

Which legacy provenance can distinguish deselection from deliberate provider denial?
Which generic scoped override/migration version and host guard seams need qualification?

## Changelog

| Date | Change | Evidence |
| --- | --- | --- |
| 2026-10-13 | Initial IUNI1–6; existing ratified direction unchanged. | Current image setup and host boundaries; proposed checks are not acceptance evidence. |
| 2026-11-05 | Integrate the retained consumer implementation with current host boundaries; retain draft status and exact-source limits. | Source review only for the new integration; affected runtime/browser checks and generated assets remain pending. |
