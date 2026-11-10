# Shell customization: status and next milestones

Source audit: 2026-09-21 UTC, Microsoft Unified `e2dd77d1` (package 0.19.15).
Implementation follow-up reconciled with main `82b9157e` (0.19.17):

- [PR #58](https://github.com/microsoft/amplifier-unified/pull/58), head
  `f551e7dd`: complete theme definitions/backgrounds, saved-appearance startup,
  compact conversation errors and shared automatic-name controls. The host
  suite passed 1,462 tests with 56 skips; 220 frontend tests and production
  browser proofs for themes, restored controls, details and mobile integration
  passed.
- [PR #60](https://github.com/microsoft/amplifier-unified/pull/60), head
  `aa54383d`: named component contributions, bounded data/action contracts,
  generation/dirty safeguards, SDK, skill and independent hot-loading example.
  The host suite passed 1,472 tests with 56 skips; 220 frontend tests and
  production browser proofs for component changes and mobile integration passed.

October 9 reconciliation: the theme and component implementations described
above are present in main `f34c2c35`. The historical test counts above describe
their original checks, not current acceptance. See [Appearances](appearances.md)
for the current product umbrella and shipped-versus-proposed boundary.

## Baseline and reviewed extensions

| Surface | Current contract | Remaining boundary |
| --- | --- | --- |
| Workspace manager and chat list | Registered navigation modules, including custom modules and up to 12 instances with stable IDs | Baseline snapshots/actions are bounded to navigation; PR #60 adds the named slots below |
| Artifact content viewers | Validated renderer packages selected per canvas view; one selected artifact per client | A renderer does not replace canvas tabs, toolbar, view routing or the whole canvas container |
| Interactive conversation surfaces | Sandboxed HTML/CSS/JS, typed state and events, stable identity, revisions, restore and shared user/agent interaction | Conversation artifacts, not installable shell modules; privileged requests currently allow only theme preview/apply/revert |
| Appearance and layout | Complete CSS skins, palette changes, scheme, density, accent and three layout presets | PR #58 adds complete light/dark definitions and backgrounds; extensible layouts remain future work |
| Header, app actions/status, composer actions, canvas toolbar, Appearance and additional Settings sections | PR #60 registers replaceable outlets and built-in fallbacks, including mobile placement and Settings navigation | Same-origin trusted components; capability checks are not isolation |
| Whole conversation/composer, message/execution renderers, canvas container, voice implementation and Settings system | Built-in React components using existing host actions | Still require replacement contracts; separate source files do not make them registered modules |

The existing loader is real: stage, host validation, prepare, preview/apply,
browser activation evidence, and revert/recovery. Dirty instances defer unsafe
replacement; compatible host-owned view state can survive replacement. Native
modules are trusted same-origin code. Compatibility validation does not provide
an isolation boundary for untrusted code.

Evidence: [`shell_modules.py`](../../amplifier_web/shell_modules.py),
[`shell-sdk`](../../packages/shell-sdk/index.d.ts),
[`shell runtime`](../../components/web-client/src/shell/runtime.jsx),
[`main.jsx`](../../components/web-client/src/main.jsx), and
[conversation surfaces](conversation-surfaces.md). The navigation SDK deliberately
does not expose transcripts, composer drafts, credentials or runtime plans.

## Current issue map

Use Microsoft issue numbers for new work. Original items remain historical
discussion links.

| Microsoft issue | Original | Relationship to customization |
| --- | --- | --- |
| [#28: theme backgrounds](https://github.com/microsoft/amplifier-unified/issues/28) | bkrabach #157 | Direct theme gap addressed by PR #58; verify its release before closing |
| [#43: Canvas Apps](https://github.com/microsoft/amplifier-unified/issues/43) | bkrabach #83 | Core surface/state/event/revision/observation behavior exists; reconcile acceptance against current code before closing. General host capability requests and common semantic controls remain bounded or incomplete |
| [#29: sidebar shortcuts](https://github.com/microsoft/amplifier-unified/issues/29) | bkrabach #154 | Keep keyboard navigation on the shared selection path and preserve dirty-view/draft guards; coordinate with sidebar work |
| [#39: execution observability](https://github.com/microsoft/amplifier-unified/issues/39) | bkrabach #88 | A useful consumer of execution/status component contracts; current summary detail levels do not fulfill all requested scopes and metrics |
| [#38: voice catalog](https://github.com/microsoft/amplifier-unified/issues/38) | bkrabach #89 | A future voice/settings contribution using provider capabilities and shared actions |

[Work parity #31](https://github.com/microsoft/amplifier-unified/issues/31) and
[integration PR #19](https://github.com/microsoft/amplifier-unified/pull/19) contain
related pending work. Presence on a migrated branch is not evidence of release
or of an extension contract. Check their current state separately.

## Complete theme definition: implemented in PR #58

At the audit baseline, palette patches retained the old CSS gradient, explaining
why a new palette could show the previous theme's decorative background. PR #58
adds versioned complete light/dark palettes and optional gradients, patterns or
embedded raster artwork. A per-client decoration preference is separate from
the theme. Complete application compiles from the built-in skin, while legacy
palette patches remain supported without accumulating CSS declarations.

UI controls, agents and Canvas App requests share preview/apply/revert paths.
Browser checks cover the preview/application match, background toggle, system
changes, saved light appearance on a dark device, and reload with open Settings
and a multiline draft. Delayed startup also reattaches completion acknowledgement
and earlier-history scrolling. Conversations and drafts remain host-owned. A
custom theme chooser remains an agent-created demonstration, not a shipped app.

## Then extend the component contracts

1. **Named contribution points and a discoverable catalog: implemented in
   PR #60.** `shell.inspect` describes slot cardinality, placement, registered
   built-ins and semantic command schemas. Added slots are `app.actions`,
   `app.status`, `conversation.header`, `composer.actions`, `canvas.toolbar`,
   `settings.appearance` and `settings.section`. Packages can replace defaults
   or include the default explicitly to supplement it. Query the installed
   host rather than assuming a pending PR's contract is available.
2. **Move the built-ins onto those same contracts.** Keep one shared host action
   implementation for each operation. Extract the fixed canvas container into
   a replaceable presentation adapter over host-owned view identity, selection,
   resource binding and dirty-navigation guards. Preserve mounted views when
   changing their placement. Coordinate extraction with responsive sidebar and
   Settings work instead of doing competing rewrites.
3. **Add conversation contracts deliberately.** Separate message renderers,
   execution renderers, conversation header, composer attachments/actions, then
   replacement composers and the conversation pane. Expose bounded, revisioned
   projections and shared send/cancel/edit/fork/upload actions. Keep canonical
   history, streaming, delivery receipts, drafts and runtime ownership in the
   host. A replacement composer must preserve attachment and input-method state
   and submit exactly once through the existing admission path.
4. **Add layout profiles and coherent presentation packages.** A package may
   contribute modules, compatible slots, a layout and a theme. Validate the
   complete composition before activation, with declared compatibility and
   fallbacks. Extend the existing preview/revert protocol across those changes;
   do not add a second persistence or activation system. Keep per-client preview
   targeting and explicitly define the scope of committed theme preferences.
5. **Provide isolated execution before accepting untrusted shell modules.** The
   current trusted-native profile remains explicit. Sandboxed Canvas Apps are
   useful prior art, but they do not automatically isolate shell modules.

For every new contract, require UI/agent parity, stale-revision handling,
idempotent mutation receipts, dirty-state handling, unload cleanup, accessible
fallbacks, and keyboard/mobile acceptance. Keep a host-owned recovery path so a
broken replacement can be removed without loading it again. Schema changes need
an explicit state-migration policy; arbitrary React local state is not durable.

Preserve economical agent observation: scoped subscriptions and compact revision
or dirty notices, followed by explicit reads when needed. Large content and
images should not be pushed into every request, and edits alone should not
start model turns. Reuse the existing surface observation contract where it fits.

## Authoring and canonical sources

The authoritative skill remains in `skills/amplifier-shell/`, loaded by
`behaviors/unified-shell.yaml` and the Microsoft skills behavior. Update the
SDK, action discovery, skill, examples and validation fixtures together as each
contract actually lands. Do not teach proposed slots as available capabilities.

Current Unified, Work, loop-live, tool-exec and optional TUI sources are under
`microsoft/*`. Runtime manifests, built-in behaviors, feedback and update defaults
must continue to use those destinations. Historical evidence links and legacy
feedback receipt recognition can retain `bkrabach/*`; saved session snapshots
must not be rewritten merely to normalize repository names.
