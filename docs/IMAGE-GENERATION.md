# Image generation across Amplifier Unified bundles

This guide describes the current source candidate, not a shipped release or
deployment. The [routed-image host contract](../contracts/image-capability-host.v1.md)
remains draft and unfrozen. The candidate removes image-only selection UI and
implements legacy-intent translation; combined offline qualification remains
pending. Neither source inspection nor previous named-backend tests establish
live account entitlement or acceptance of the new routed path.

The shared Image generation behavior exposes a tool and skill across ordinary
root bundles. New unpinned requests use the session's model routing and permitted
ordinary provider connections, independently of the conversation's selected
chat model. Unified owns composition, admission, file policy and saved outputs;
Imagegen owns operation identity and files; routing owns candidate order; each
provider owns its native image execution. Unified does not rank image providers.

The portable [imagegen bundle](https://github.com/microsoft/amplifier-bundle-imagegen)
owns the module, library, skill and brief capability guidance. It does not change
the selected root, orchestrator, context manager or conversational provider.
Mounting it makes no image request and creates no account or credentials.
Without an explicit `backend`, the module mounts `RoutedImageTool` using the
`image-gen` role. An explicitly retained backend mounts `WorkspaceImageTool`
instead. Missing image support is reported by capabilities and does not prevent
ordinary chat. Requested generation/editing remains subject to paid-call and
host admission policy.

## Use ordinary connections and routing

1. Use **Settings → AI connections** for ordinary account connections and the
   existing model-routing controls for policy. There is no **Manage image
   generation** menu or image-only account/model selector in this candidate.
   `providers.configureImages` is retired and absent from the action schema;
   calling it is not a supported migration or save path. Generic scoped
   `providers.save` and `routing.use` remain the configuration boundaries.

   A new routed request prepares an exact mounted provider through Foundation's
   operation-routing helper and negotiates the optional
   `amplifier.image-operation.v1` surface. The role's conversational model is
   separate from its nested native image model/transport. Current coordinated
   provider candidates default to OpenAI Responses and Gemini Interactions;
   explicit compatibility transports are provider-owned, not host fallback.
   A vision-capable chat model, ChatGPT sign-in or another chat subscription
   does not by itself grant access to a different image API.

2. **Settings → Advanced → Configured bundles** shows **Image generation** as an enabled app behavior
   by default, including configurations with an older saved app-behavior list or
   an empty list. It applies to any ordinary root bundle without changing the
   order of existing behaviors. An explicit disable or removal is retained.
   Disable or remove it through the same controls used for other behaviors.
   To restore a removed capability, use **Add capabilities**, role **behavior**,
   with this source:

   ```text
   git+https://github.com/microsoft/amplifier-bundle-imagegen@main#subdirectory=behaviors/imagegen.yaml
   ```

   The behavior permits requested image calls through the selected route or
   retained explicit backend. Keep the conversation's chosen root bundle.
   The same agent path is `bundles.add` with this URI and `role: behavior`;
   read its schema first.

3. Start a fresh conversation, keeping the desired root and chat model.
   Ask it to inspect `image_generate` capabilities. On the routed path,
   `ready: true` means local prerequisites exist, with `route: not_selected`.
   Discovery does not resolve a route, list remote models or generate.
   Compatibility is checked for the actual request before dispatch; entitlement
   stays unverified until that selected connection completes a requested call.

If the tool is absent, the optional behavior is not active in that runtime. If
capabilities report `model_role_resolver` or `image_operation_provider` missing,
inspect the composed routing capability or installed provider support rather
than inventing a new image account. `legacy_image_selection_requires_review`
means migration retained an unresolved restriction. On the explicit-backend path,
`selected_image_backend_not_mounted` means that retained backend is unavailable;
it must not silently switch to routing. Existing running sessions keep their
mounted capabilities until refreshed through normal host controls.

`providers.imageModels` remains read-only diagnostics for a saved connection,
not a selector. It may read that account's model catalogue, but generates no
image, changes no settings and grants no entitlement. Empty catalogues, errors
and unsupported discovery remain distinct from successful generation.

The existing Work image behavior/preset forwards to the same portable behavior.
Composing it in both a root and the host does not add another image tool. An
imagegen host behavior retains an already-declared image tool's source/configuration,
including paid-call opt-outs and named instances, while adding image skill discovery.
Other skill directories and explicit module settings remain effective. Saving an
explicit app-behavior list does not change this preservation rule. Other behavior
overlays keep their normal precedence.

The bundle and module sources track `main`. Normal source overrides and generation
qualification apply. Git access to the bundle repository is required to resolve it;
an inaccessible source is a setup failure, not proof of a missing image account.
Saved snapshots remain complete plans and are not injected with new behaviors.
Active worker generations keep their mounted capabilities until normal refresh.
Fresh-plan translation does not write saved settings. The explicit scoped
reconciliation action below does change its confirmed source preference.
Acceptance fixtures use isolated settings, not production settings.

## Legacy intent: fresh-plan translation, not settings migration

`amplifier_web/image_migration.py` translates effective legacy settings only for
new, unbound mount plans containing `tool-image`. It does not write global,
project or local settings, modify provider configuration, probe credentials or
rank connections. Source settings remain the rollback record. Saved snapshots
and already credential-bound plans do not enter this translation.

| Existing intent | Candidate translation |
| --- | --- |
| `image_generation.mode: off` or tool `allow_paid: false` | Preserve denial; no paid-call enablement |
| Explicit tool `backend` with mode absent or recognized, subject to opt-out | Retain that compatibility backend, not a new route |
| One enabled legacy provider pin, with mode absent or `selected` | Retain its registered backend ID and unchanged provider model/configuration |
| Unresolved selection, multiple pins or ambiguous disabled/unavailable legacy state without a retained backend | Set `routing_blocked: true`, result `ambiguous_requires_review` |
| Unsupported legacy mode | Set `routing_blocked: true`, result `unsupported_legacy_mode` |

An unpinned fresh configuration can use routing without an image setup step.
Old `enabled: false` values are not automatically reclassified as either harmless
deselection or permission to use another account. Review intent through existing
scoped configuration before changing it; migration itself supplies no approval.

The plan carries `image_routing_migration` version 1 with `sourceMode`, a
`source` list of `providerInstance` plus `image: {enabled, id, model}` fields
when present, and a result. It excludes broader provider configuration and
credentials. This is an
effective-plan receipt, not a replacement for original scoped source records.
Reruns are intended to preserve the same plan and receipt; exact idempotence and
scope preservation require the candidate's migration checks.

### Explicit reconciliation of a retained Off preference

**AI connections** and **Modules & sources** show scoped capability diagnostics.
`modules.diagnostics` reports retained restrictions without changing them.
`modules.reconcile` requires the exact source scope, a fresh diagnostic revision
and explicit confirmation. Its supported `inherit` resolution removes only that
scope's retained `image_generation.mode: off`; it preserves other fields,
provider/model pins and restrictions from other scopes. Unknown intent refuses.

This action does not grant paid access, select an account, or refresh an existing
conversation. New chats use the remaining composed policy. A stale diagnostic,
missing confirmation or refused configuration authority leaves the source intact.

## Generation, edit and delivery

The tool returns a workspace PNG, SHA-256, and `receiptPath`. Use the shared
`outputs.attachImage` action to save the exact completed receipt and image. Supply
the originating `messageId` to show the image inline in that turn; a later message
never becomes the origin automatically. Omit it to save in the library only.
The saved output and Canvas share one immutable image body, including when the
conversation is forked. Saving does not open Canvas or replace an unsent draft. For
an edit, attach the original first and supply its saved `parentId`; the target hash
in the receipt must match that exact parent. Generated files never replace inputs.
The standard outputs library exposes the saved versions and downloads. Call
`outputs.image` directly through `app_control` to send their exact saved PNG pixels
to a vision-capable model. Receipt text and browser display are separate evidence.

Inline generation placeholders reflect observed `image_generate` generate/edit
calls with a known request ID and originating turn. Delegated calls require a
recorded parent chain back to that turn. The `nano-banana` generate/edit path also
shows progress using its observed call identity; analysis calls do not.
Capabilities and status checks do not create placeholders. Request-linked results
appear after the work and responses for their originating request, before the
next request. Animation stops when execution stops, and completed
saved images replace the matching placeholders. No estimated progress or automatic
generation retry is inferred from these displays.

The host passes effective filesystem read and write restrictions to the image tool,
including root and child declaration denials. Image access stays inside the
execution workspace. Receipt metadata is producer-reported provenance; the host
independently verifies bytes, dimensions and hashes, not the provider account's
remote identity. No arbitrary URL download or publication is performed.

Interrupted image calls may still have cost or a server-side effect. Their durable
request IDs are never automatically replayed. Read `image_generate` status for the
same ID and reconcile an unknown outcome before deliberately requesting new work.
Optional `generate_image` calls traverse the host's provider-call admission,
cancellation and observation boundary; wrappers forward optional methods rather
than manufacturing support. Noncompleted provider results are not shown as
completed calls. Telemetry uses the image model identity and normalized reported
usage, not prompts, image bytes or private continuation. Saved receipts retain
native usage when supplied. Unknown usage/cost stays unknown; this does not add
an image spend cap, quota enforcement or a claim that ordinary chat-token limits
cover image costs.

## Qualification boundaries

Offline qualification must cover removed action/UI paths, scoped non-writing
migration, generic routing, exact-instance optional methods, denied dispatch,
unknown outcomes, saved receipt compatibility and unchanged chat selection.
Fresh installed-host composition and saved-plan reconstruction need separate
checks; a source-only unit fixture is not proof of the deployed browser.

The coordinated CLI candidate also composes the external image behavior during
fresh resolution, below the selected root, preserving explicit backend and paid
opt-outs. Its generic configurator can unmount the tool; that does not suppress
bundle downloads or rewrite saved plans. CLI composition is not Unified browser
acceptance, and neither proves the other host's child-policy behavior.

The existing `scripts/work_profile/image_generation_acceptance.py` is a retained
**named-backend** harness: with explicit live authorization it makes at most two
low-quality 1024×1024 OpenAI Images API calls, then checks saved images, parent
hashes and pixel retrieval. It does not exercise routed Responses/Interactions,
natural skill selection or the browser. Likewise, `image_setup_acceptance.py`
uses synthetic credentials and an explicit provider backend to inspect fresh
Work composition; it does not qualify the new zero-image-settings route.

`automatic_image_acceptance.py` targeted the retired host-owned provider
selection path. It now refuses before reading credentials, creating output
directories or making a request. Its historical results are not routed
acceptance; selecting a different paid test requires its own authorization.

Keep those historical evidence boundaries instead of treating them as new routed
acceptance. Combined candidate qualification remains pending. Live generate/edit
requires an explicitly authorized account and bounded calls; actual pixel review,
saved-output reload/download and originating-turn browser delivery are additional
observations. No live request, deployment or newly passed test is asserted here.
