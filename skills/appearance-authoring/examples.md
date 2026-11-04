# Appearance authoring examples

These are inputs and review cases, not commands to execute automatically. Read
the installed action schemas before adding required client or revision fields.
Companions resolve from the loaded skill directory.

## New complete theme: Quiet study

Read [quiet-study.json](quiet-study.json). Its `name` and `definition` form a
theme input. Both palettes contain the ten required six-digit hex values.
`background` is omitted deliberately: this clears previous artwork rather than
retaining another skin's decoration.

After schema discovery, the dispatch envelope has this shape:

```json
{
  "operation": "dispatch",
  "args": {
    "action": "theme.preview",
    "args": {
      "name": "Quiet study",
      "definition": {
        "version": 1,
        "palette": {
          "light": "...read the complete companion...",
          "dark": "...read the complete companion..."
        }
      }
    }
  }
}
```

The abbreviated palette above is explanatory, not a valid payload. Use the
complete companion unchanged as the starting input, then include any target
fields required by the discovered schema. Preview does not apply or save it.
Use separate supported operations for those requested effects.

## Adapt a complete CSS skin without losing its identity

Read the current saved CSS through the host. Copy it into the requested output,
preserving the original. Change documented custom properties within its existing
light and dark rules, for example:

```css
/* Merge into BOTH existing scheme rules, not a standalone full skin. */
--a-composer-radius: 12px;
--a-card-radius: 10px;
--a-message-radius: 10px;
--a-message-gap: 24px;
--a-panel-gap: 7px;
--a-panel-radius: 10px;
```

Send the complete resulting stylesheet as `css`, not only this fragment. Do not
combine `css`, `tokens` and `definition` in one theme request. Check narrow mode:
host-owned responsive spacing and target sizes take precedence.

For only an accent-color edit, prefer `tokens` and explicitly retain the current
background. Do not claim that a partial palette edit created a complete theme.

## Share styling with local assets

Use an ordinary ZIP for assets that would be awkward to maintain inside one CSS
file. Follow the full manifest and limits in the linked theme guide. This is a
styling package, not a shell/component installer.

```json
{
  "type": "appearance",
  "formatVersion": 1,
  "name": "Quiet studio",
  "stylesheet": "theme.css",
  "assets": [
    {"path": "assets/paper.png", "mediaType": "image/png", "license": "licenses/artwork.txt"}
  ],
  "licenses": ["licenses/artwork.txt"]
}
```

Put this at root `appearance.json`, next to `theme.css`. Include the exact asset
and notice bytes at the declared paths. Reference the image with
`url("assets/paper.png")` from that root stylesheet. Import validates and embeds
supported references; it does not download missing assets.

After discovering the installed schema, use `theme.import` with `packageBase64`
containing the actual base64 ZIP bytes and no `css` argument. Check the returned
library entry, preview its compiled CSS, and apply only when authorized. Export
that saved ID with `format: "zip"` to retain its original assets and notices.
Default/no-format export remains CSS. Inspect a real downloaded ZIP and reimport
it into disposable storage before claiming a working portable package.

Do not infer font rights from an OFL-looking filename. Retain the upstream
copyright and license text, source revision and file hashes. For modified or
subset fonts, check reserved names and format-conversion conditions. Render
realistic text and controls in both schemes before choosing fonts; close-looking
alternatives need not all become bundled dependencies.

## Selectors that preserve the host

Inspect the current DOM; these selectors are examples from the bundled web shell,
not permission to override its layout:

```css
/* Change the bubble, not the row, footer, avatar or timestamp. */
#amp-one .a-message.a-user > .a-message-body {
  background: var(--a-tint);
  color: var(--a-ink);
}
/* Assistant padding belongs inside its body; actions remain outside. */
#amp-one .a-message.a-assistant > .a-message-body {
  padding: 12px 16px;
}
/* The input surface is not the outer form, model selector or status row. */
#amp-one [data-part="composer-surface"] {
  background: var(--a-surface);
}
```

For typography, inspect computed styles on actual message `p`/`span` elements.
Changing only the shell font does not establish that chat text changed. Use a
system mono stack when requested; do not add a network font dependency. Keep UI
labels readable rather than applying chat typography indiscriminately.

**Do not use** `conversation > * {position:relative; z-index:1}`. This moved the
absolutely positioned navigator into normal flow and created a large gap.
Do not compensate with guessed margins. Prefer the documented
`--a-conversation-art` token for quiet artwork at the top of the conversation.
For a custom full-panel decorative background layer, leave child layout alone:

```css
#amp-one [data-part="conversation"] {
  position: relative;
  isolation: isolate;
}
#amp-one [data-part="conversation"]::before {
  content: "";
  position: absolute;
  inset: 0;
  z-index: -1;
  pointer-events: none;
  /* Supply quiet artwork here; leave all child positioning untouched. */
}
#amp-one[data-decorations="off"] [data-part="conversation"]::before {
  display: none;
}
```

For digital rain, three transparent embedded PNG tiles with uneven glyph spacing
give depth without three DOM layers. Animate `background-position` by integer
multiples of each tile's height for seamless loops; use distinct layer speeds.
Keep light-mode glyphs subdued emerald, not neon glow. Tune density separately
from opacity. Respect `prefers-reduced-motion: reduce` with static artwork and
`forced-colors: active` with decoration removed. Custom pseudo-elements need their
own decoration-off rule; the host cannot infer which custom pixels are artwork.
Keep decorative lines away from labels and controls.

### Inherit transcript edges, do not copy the mask

```css
#amp-one {
  --a-conversation-art: linear-gradient(135deg, var(--a-tint), transparent 65%);
  /* Omit transcript fade tokens and mask rules: use the shared baseline. */
}
```

The host's message scroller owns conditional 18px top/24px bottom fades and
forced-colors handling. Keyboard-visible focus and active editors suppress the
mask; pointer/programmatic focus on a non-visible container does not. Optional
`--a-transcript-fade-top` and `--a-transcript-fade-bottom` tune lengths within
host limits; first confirm support in the installed baseline. No custom mask,
overlay or scroll listener is needed. Short content must stay unfaded, and the
bottom fade must disappear at the end. Preserve the scrollbar gutter, navigator,
composer and final message actions.
Remove obsolete masks only from the bundled skin being updated, not saved user
copies. Background artwork is independent and need not be removed.

### Optional logo tint with a matching high-contrast reset

Keep the original host image. If a tint is requested, match the specificity of
both scheme rules in the reset; a less-specific `#amp-one .a-brand img` reset
loses to a scheme-qualified filter even when it appears later:

```css
#amp-one[data-theme-scheme="light"] .a-brand img {
  filter: sepia(1) saturate(2) hue-rotate(120deg);
}
#amp-one[data-theme-scheme="dark"] .a-brand img {
  filter: sepia(1) saturate(2) hue-rotate(120deg) brightness(1.2);
}
@media (forced-colors: active) {
  #amp-one[data-theme-scheme="light"] .a-brand img,
  #amp-one[data-theme-scheme="dark"] .a-brand img {
    filter: none;
  }
}
```

The host resolves Device/system preference into `data-theme-scheme="light"` or
`"dark"`. Supply explicit rules for both. Do not let an unqualified OS dark-mode
media query override the user's explicit Light choice. Check all four combinations
of OS light/dark and explicit Light/Dark, plus Device following each OS mode.

## Small acceptance run

1. Preview Quiet study over a theme with artwork. Confirm flat backgrounds in
   light and dark; check system scheme follows the OS.
2. Use long chats, a multiline unsent draft, code, buttons and an error fixture.
   Check contrast, focus, navigator, bubble/footer bounds, input/model-selector/
   status separation and local feedback at narrow and wide widths plus zoom.
   Check transcript edges at top/middle/bottom and with short content. A pointer
   **Review questions** action must retain fades when it focuses a non-visible
   section; keyboard-visible focus and active editing must suppress them. Final
   text/actions must be fully visible at the bottom. Static swatches cannot
   establish these results.
3. Toggle decorations in a disposable client, restore its initial preference,
   and check reduced motion/forced colors. Record unsupported checks as unknown.
4. If applying is authorized, compare committed output with preview and reload.
   Revert through the host and verify the starting appearance returns without
   altering conversations or drafts.
5. Export the saved id, import into disposable library storage, read back exact
   CSS/metadata and preview. Check stale fingerprints reject update/delete and
   included entries remain read-only. A library save is not an apply or render.

Keep evidence separate: package validation, loader discovery, browser rendering,
interaction checks and user acceptance are different results. The example palette
is a starting point, not certified accessibility or whole-app conformance.

For a bundled addition such as Lightline, also check that the packaged stylesheet
and metadata are discovered unchanged and saved user copies remain untouched.
Its luminous ivory/platinum and obsidian/cyan palettes, warm user bubble, mono chat and
static architectural treatment are a reference, not a mandatory style for new
appearances. Inclusion never changes the user's current selection or preferences.

For reference-led work, choose a real light-scene/material reference as carefully
as the dark one. Lightline uses illuminated glass, white lacquer, silver and
acrylic as light-mode materials, with graphite text and a dark blue-gray control
accent for readability. These are authored adaptations, not sampled film colors.
Keep geometry coherent across schemes, then verify each scheme's text, controls,
selection and focus independently. A name change or desaturation alone is not
light-mode design.