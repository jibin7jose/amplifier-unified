# Customize an Appearance

[Appearances](../../docs/shell/appearances.md) is the product umbrella for themes,
layout, registered components, viewers and interaction feedback. Follow the
installed contracts below; do not invent a single appearance-package API.

1. Call `shell.inspect` for the target client. Record its revision, composition,
   package availability and browser status. Query affected instances with
   `shell.query`. Use the current schemas rather than copying an old payload.
2. For module order, visibility, supported layout, or presentation tokens, form
   a complete proposed composition preserving unaffected instance IDs and
   settings. Prepare it with `shell.changes.prepare` and the inspected revision.
   Existing modules can have multiple instances where their contract permits.
3. Preview the returned change, inspect its browser report, then apply within
   the user's requested scope. Preview does not commit a composition. For a
   custom CSS skin, discover the theme service's current actions and use those.
4. Reinspect after activation. An `awaiting-browser` receipt is not completion.
   Confirm the matching revision mounted, and verify the intended interaction.
   If stale, inspect and prepare again; do not repeatedly force old revisions.
5. Exercise unsent drafts, chat switching, selected conversations and any dirty
   editor/viewer affected by the change. Retain the change ID and current
   revision for `shell.changes.revert`.

**Success criteria:** The intended client shows the change, host-owned
conversation/draft data is preserved, and revert restores the prior composition.

For canvas work, use `canvas.views.inspect` to obtain exact view/resource IDs,
resource revision and generation. Activate a compatible viewer through
`canvas.views.renderer`, not the navigation composition. Include the current
target fields required by each action. The host currently supports up to two
artifact views; live MCP App bindings have a separate primary-view constraint.
Read the canvas sections of [the shell guide](../../docs/shell/README.md) before
changing viewers, dirty state, or recovery.

Discover registered contribution points in `shell.inspect.slots`; see
[component contributions](../../docs/shell/components.md). Do not promise
replacement chat composers or whole conversation decomposition unless the
installed schemas and SDK explicitly expose them.

## Themes

Follow the [appearance and interaction contract](../../docs/shell/appearance-contract.md)
for busy/disabled states, detail levels, motion, contrast and focus. Pending
activity does not authorize tinting or locking an entire region. Theme changes
must preserve the user's accessibility and detail preferences.

Use `definition` for a new complete theme, `tokens` for an intentional partial
palette edit, or `css` for a full legacy-compatible skin. Do not apply only a
palette and claim the old theme's decorative background has been replaced.
Read [the theme contract](../../docs/shell/themes.md) for both palettes,
background values, preview scope, the separate `presentation.decorations`
preference, and verification. Discover the installed action schemas first.

For custom skins, use [appearance-authoring](../appearance-authoring/SKILL.md).
Inspect the baseline CSS and actual DOM before overriding message bodies, the
inner composer surface or logo filters; preserve outer actions and layout.
Transcript overflow fades are a shared primitive, not theme-owned artwork:
inherit the conditional defaults and preserve keyboard-visible focus, active
editing, forced colors, scrollbar gutter, navigator and composer. Pointer or
programmatic focus on a non-visible review container alone does not disable fades.
Do not add permanent masks or reposition children to accommodate them. Keep
background artwork separate and away from controls.

Lightline is an included static architectural appearance, not a forced selection.
Bundled additions and shared-style updates must leave saved user copies, committed
custom CSS and independent presentation/accessibility preferences untouched.

A theme chooser is an agent-created conversation surface demonstration, not a
required shipped app. Reuse its surface ID while refining it and preserve its
state. Use host preview/apply actions for both user and agent selections.
