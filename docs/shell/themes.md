# Complete themes and presentation preferences

The [appearance and interaction contract](appearance-contract.md) defines the
whole-app design language, waiting/saving states, detail levels and accessibility.
This document describes the currently supported theme authoring API.

Discover `theme.*` and `shell.*` schemas from the running host before authoring.
`theme.preview` and `theme.apply` accept exactly one of:

- `definition`: a complete version 1 theme. It starts from the host's built-in
  skin, never the previous theme's decoration.
- `tokens`: a partial palette edit preserving the current skin and background.
  On a structured theme, it updates both light and dark palettes.
- `css`: a complete existing-style CSS skin. This remains supported.

All three require a `name`. A definition contains `version: 1` and a `palette`
with `light` and `dark` objects. Each palette must supply all ten six-digit hex
colors: `bg`, `surface`, `soft`, `ink`, `muted`, `line`, `accent`, `tint`, `green`
and `danger`. Choose readable foreground/surface combinations in both modes.

The optional `background` object contains `light` and `dark` CSS
`background-image` values. These can be `none`, gradients, repeating patterns,
or embedded PNG/JPEG/WebP data URLs accepted by the skin validator. Optional
`size`, `position`, and `repeat` values default to `cover`, `center` and
`no-repeat`. Remote URLs, SVG data URLs, extra declarations and imports are
rejected. The palette's `bg` is the fallback when embedded artwork cannot load.
Omitting `background` explicitly gives a flat background. A chooser should use
these same values in its swatches and use the actual host preview to confirm
whole-shell rendering; a hand-drawn thumbnail alone is not preview acceptance.

Preview is scoped to the targeted attached browser client. Applying a theme
changes the existing app-wide committed skin. `theme.revert` restores the
previous committed theme. Keep that scope explicit when multiple clients are
attached; changing a theme does not change their conversation selection.

The shell composition's `presentation` accepts `scheme` (`light`, `dark`, or
`system`) and `decorations` (boolean, default true). Change these with the
existing prepare/preview/apply/revert protocol. Turning decorations off hides
background artwork without editing the theme, so turning it on restores it.
These are client presentation preferences, separate from the committed skin.
Appearance settings expose the same decoration preference to the user.

Canvas Apps can request the same structured `theme.preview` / `theme.apply`
payloads through their declared host actions. The host compiles and validates
at request time, so approval applies the exact reviewed stylesheet. Existing
request review, cancellation, stale revisions and client targeting still apply.

On reload, a small per-tab cache colors the loading screen using the last
committed scheme and colors. It contains no transcript or raw stylesheet. The
app waits for its authoritative shell preferences before rendering the shell;
previews are never saved into this loading-screen cache.

## Window chrome

The compact application header uses `--a-chrome-bg` and `--a-chrome-ink` when
supplied by a CSS appearance, falling back to the existing `--a-bg` and
`--a-ink` tokens. These are optional CSS properties, not new required fields
in the version 1 theme schema. Native-control clearance and drag exclusions
remain host-owned; a skin must not position controls under native buttons.

The page's `theme-color` follows the resolved, painted header on preview,
apply, revert and color-mode changes. Transparent header colors fall back to
an opaque application background. A preview affects live chrome but never the
committed startup cache. The cache is invalidated when the stylesheet changes,
so an unvisited mode cannot reuse the previous skin's chrome.

An installed desktop browser that enables Window Controls Overlay can merge
this header into its native title area. Unsupported browsers and disabled
overlay retain the ordinary header and browser-owned title bar. Window-control
placement and dragging require installed-browser/OS verification; CSS geometry
or a page metadata test alone does not establish those outcomes.

Native controls take a single opaque color, not a spatial gradient. In a
non-stacked overlay window, the host blends a matching flat header color into
decorative root artwork over 96px instead of cutting it off at the header edge.
The original background remains intact below that join. Decoration-off mode,
explicit `--a-chrome-bg`, header background images or different header colors
opt out; focused Canvas and stacked narrow layouts retain their own surfaces.
This is a softened join, not a gradient rendered by native window controls.

Acceptance must include preview/apply parity, light/dark/system, decoration
on/off, a new theme clearing earlier artwork, revert, reload, and preservation
of unsent drafts and conversations. `frontend/tests/theme-definitions-browser.mjs`
exercises the production browser against a disposable host. It makes no model
calls and does not establish acceptance on a deployed user host.

## Optional shape and spacing tokens

CSS appearances can also supply `--a-composer-radius`, `--a-card-radius`,
`--a-control-radius`, `--a-message-radius`, `--a-message-gap`, `--a-composer-pad`,
`--a-card-pad`, `--a-card-line`, and `--a-surface-shadow`. The refreshed shell
uses these for its composer, message bubbles, output cards, and controls.
Omitted tokens retain the standard geometry. Narrow conversations reduce the
message gap to 18–22px; touch-target sizes and panel layout remain host-owned.
Aurora, Atelier, and Graphite demonstrate small variations in both color modes.

### Shared transcript edges

The shared transcript baseline owns overflow cues, not individual appearances.
It uses the existing scroll owner to fade only an edge with content beyond it:
18px at the top and 24px at the bottom. Short transcripts have no fade; reaching
the bottom removes the bottom fade so the final text and actions remain readable.
Keyboard-visible focus on the scroll pane or a descendant, and focus on an active
editor (`input`, `textarea`, `select`, `[contenteditable]`, or `iframe`), disables
the mask. Pointer/programmatic focus on a non-visible container alone does not.
An active embedded review is detected from the host document's active iframe,
not only CSS focus selectors; no child-document access is required. A mounted
but inactive iframe leaves the fade enabled. Native focus changes and frame
removal clear this host-owned guard without moving the reading position.
Forced colors removes the mask. The scrollbar gutter remains intact. The navigator
and composer are outside the masked scroller; do not mask the conversation panel
or reposition its children.

CSS appearances may tune `--a-transcript-fade-top` and
`--a-transcript-fade-bottom`; omitted values retain the native defaults above.
The host clamps fade lengths to 0–48px and at most half the viewport height.
Classic scrollbar gutters retain opaque mask coverage; overlay scrollbars expose
no layout gutter, so their edge rendering needs platform-specific inspection.
These are optional CSS properties, not fields in
the version 1 theme schema. Prefer omission to copying the mask or deriving
overflow from a theme. Never add an unconditional transcript mask, an overlay
that covers the final message, or theme-owned scroll listeners. Remove duplicate
mask rules from bundled skins when the shared fallback is in place; do not
rewrite saved user copies.

Check the installed baseline before relying on these properties. This describes
the shared authoring baseline, not certification that an arbitrary skin preserves
it; check top/middle/bottom, short content, pointer-triggered question review,
keyboard-visible focus, active editors and forced colors.

### Main panel surfaces

The main screen also consumes these optional CSS tokens:

| Token | Use |
| --- | --- |
| `--a-panel-gap` | Horizontal panel gaps and space below the header; clamped to 0–16px. |
| `--a-panel-radius` | Interior upper corners. Screen-side and bottom corners stay square. |
| `--a-panel-border-width`, `--a-panel-line` | Panel outlines; default 0px with the standard separators retained. |
| `--a-panel-shadow` | Subtle depth on navigation, conversation and canvas. |
| `--a-navigation-pad` | Navigation's top padding; control target sizes stay unchanged. |
| `--a-navigation-art` | Navigation background image/gradient. |
| `--a-navigation-art-size`, `--a-navigation-art-repeat` | Artwork sizing and repetition. |
| `--a-conversation-art` | Optional quiet artwork across the top 240px of the conversation. |
| `--a-canvas-art` | Canvas header artwork. |

Aurora uses 10px gaps, 22px interior corners and a faint glow; Atelier uses
7px gaps, 10px corners and paper-like edges; Graphite uses 4px gaps, 4px corners
and a fine grid. The original Amplifier appearance retains its continuous frame.
Theme changes remeasure panel space immediately, including previews. Narrow
screens use an uninterrupted surface; focused and overlay canvases stay square.
The background-artwork switch hides these panel decorations as well as root
artwork, while retaining shapes, borders and spacing. Prefer static decorations.
Keep decorative lines away from navigation controls and labels; selected-row
indicators are functional feedback, not background artwork.
The user-directed Digital Rain design intentionally uses continuous optional
code-rain artwork. This is not a work-status or busy-region effect: it changes
no layout and conveys no operational information. Such explicitly selected
artwork must be non-flashing, remain behind readable content, stop under reduced
motion, and disappear with decorations off or forced colors. The interaction
contract's restrictions on animated busy-region backgrounds, tint, pulse and
operational feedback remain unchanged.

### Create, edit, delete and share

Open **Settings → Appearance → Save, import & export themes**. The ordinary
Appearance cards choose a shared committed look immediately. In the library,
selecting a card previews it only in your attached browser. Leaving Appearance
or closing Settings clears that preview. Library operations never apply CSS.

| Intent | Settings | Shared agent action |
| --- | --- | --- |
| Create or copy | Expand **Advanced customization**, name the CSS, choose **Save as copy**. | `theme.save({name, css, description})` |
| Import | **Upload appearance** accepts portable `.css` or an appearance ZIP; it keeps embedded metadata. | `theme.import({css})` or `theme.import({packageBase64})`; supply exactly one. Optional `name`/`description` override metadata; `fallbackName` names CSS files with no embedded name. |
| Edit | Select an entry, choose **Edit**, change name/description/CSS, then **Update saved appearance**. | Read with `theme.read({id})`, then `theme.update({id, expectedFingerprint, name, css, description})`. |
| Delete | Select a saved entry, choose **Delete**, then **Confirm delete**. Export first to retain a copy. | `theme.delete({id, expectedFingerprint})` |
| Export | **Export [selected name]** exports CSS; **Export [selected name] as ZIP** includes package assets and notices. Current-appearance controls use committed CSS, not edits or previews. | `theme.export({id, format: "css"})` (default) or `theme.export({id, format: "zip"})`; omit `id` for committed appearance. |

`theme.list` returns metadata and fingerprints, without CSS. `theme.read` returns
the current self-contained CSS and a `fingerprint` for that saved version. Keep that fingerprint with
the editor draft; do not silently replace it with one from background refresh.
Settings keeps the target, fingerprint, name, description and CSS together in a
tab-private editor draft. Another client's Apply or a background library refresh
cannot replace that draft. It survives visiting unrelated Settings sections,
closing/reopening Settings and reloading the same tab through browser session
storage. A storage failure is shown explicitly; keep that tab open and save a
library copy. Previews do not resume automatically. The renderer uses the exact
validated preview CSS, not whatever the editor contains after further edits.
The preview banner and its confirmation apply that same snapshot, including
agent-requested previews. **Apply appearance** in the editor remains a distinct
action on the retained draft. Cancelling a preview does not discard that draft.
Reloading or duplicating a page ends its inherited preview, without cancelling
another open page's preview or changing either page's saved preferences.
Updates retain the saved ID, including a rename. A stale update or deletion fails
with 409 and leaves the file unchanged: read it again, review differences, then
decide which edits to retain. Included appearances are read-only; copy them instead.
Deleting or updating a library entry leaves already-applied CSS intact across
restarts. Apply an edited version separately.

Imports never silently overwrite another entry. Identical canonical uploads
reuse the same ID; changed content creates another entry even with the same name.
If an in-place update occupies an older export's content-based filename, importing
that export uses a deterministic unused suffix. Reimporting the identical export
reuses that entry instead of replacing the updated appearance.
The portable file carries one leading metadata comment:

```css
/* amplifier-appearance: {"name":"Quiet Studio","description":"Warm surfaces and calm accents."} */
#amp-one { --a-accent: #316653; }
```

Saving/exporting preserves metadata (including additional author-provided fields)
without nesting headers. Imports accept UTF-8 with or without a BOM and LF or
CRLF line endings. Export writes one canonical metadata header and retains the
CSS body. Artwork and fonts must be embedded in the CSS; remote
URLs, filesystem URLs, SVG data URLs and external imports are rejected by the
same validator used for preview/apply. The file including metadata is limited to
1 MB. Imports are published atomically; symlink files/directories, traversal IDs
and nonregular files are rejected. Cooperating host writers serialize updates.
An external editor should use the shared actions when it needs stale-write
protection; arbitrary filesystem writers do not participate in that lock.

Export returns the exact downloadable CSS in the action result and retains the
existing attached-client download effect. With no ID, it preserves the established
exact committed-CSS export. To share a named library entry, export it by ID and
upload the file on the recipient's installation. This transfers a theme only,
not shell layout, accessibility preferences, credentials or executable components.
An import is validated, not certified accessible: preview both schemes, reduced
motion, decorations off and a narrow layout before applying.

Digital Rain is included as a complete packaged stylesheet with embedded artwork.
It is loaded unchanged, rather than rebuilt using another preset's geometry.
Bundled updates do not overwrite edited saved appearances.

Lightline is an included, neutral architectural appearance: luminous ivory,
platinum and graphite in light mode, obsidian and cyan in dark mode, a warm user bubble, monospaced
chat and readable UI text. Its surface highlights are static. It inherits the
shared transcript edges rather than supplying a permanent mask. Included means
available to choose, not a new selection for existing users. Shipping it must not
replace saved copies or change scheme, decorations or accessibility preferences.

Both palettes have an intentional material reference, rather than treating light
mode as a pastel inversion. The light treatment abstracts illuminated glass,
white lacquer, acrylic and silver; dark uses obsidian with cyan edge light.
The [production-design account](https://www.latimes.com/archives/la-xpm-2010-dec-18-la-hm-tron-movie-set-design-20101218-story.html)
informed those materials, not sampled color values or copied imagery. Functional
accent colors are readability adaptations. A reference does not replace contrast
measurements and rendered review in either scheme.

Breadbin is a static, home-computer-inspired appearance with warm ivory casing
and deep violet accents in light mode, a blue-violet conversation screen and
smoked casing in dark mode, and an optional four-color header edge. It uses
system UI text and readable monospace conversation, not a bundled retro font.
Keycap depth is paint only; pressed controls keep their dimensions. Message
actions stay outside the bubble bodies, and transcript fades remain host-owned.
Adding the bundled entry does not apply it or replace saved personal versions.

For a bundled appearance change, keep qualification focused: confirm the packaged
CSS and metadata load unchanged, preview the actual host in both schemes and a
narrow layout, check decorations/reduced motion/forced colors and essential
controls, then verify preview/apply/revert and saved-copy preservation. Record
what ran and what remains unchecked; packaging alone is not visual acceptance.

## ZIP appearance packages

Use an ordinary `.zip`, such as `quiet-studio.zip`. Contents define its type, not
the filename. Version 1 transports a complete stylesheet, supported local assets
and license notices. It is not a general archive viewer, plugin installer or
portable shell composition.

```text
quiet-studio.zip
├── appearance.json
├── theme.css
├── assets/
│   ├── background.png
│   └── reading.woff2
└── licenses/
    ├── artwork.txt
    └── OFL.txt
```

```json
{
  "type": "appearance",
  "formatVersion": 1,
  "name": "Quiet studio",
  "description": "Quiet surfaces with locally bundled assets.",
  "author": "Example author",
  "stylesheet": "theme.css",
  "assets": [
    {"path": "assets/background.png", "mediaType": "image/png", "license": "licenses/artwork.txt"},
    {"path": "assets/reading.woff2", "mediaType": "font/woff2", "license": "licenses/OFL.txt"}
  ],
  "licenses": ["licenses/artwork.txt", "licenses/OFL.txt"]
}
```

`type`, `formatVersion`, `name` and `stylesheet` are required. Optional descriptive
fields carry attribution, not a verified authorship claim. Every archive member
must belong to the manifest, stylesheet, declared assets or declared notices.
Unsupported versions and unknown manifest fields are rejected. Fonts require a
bundled license reference; the host checks the presence of notices, not whether a
license grants the submitter every right they claim.

Stylesheets reference declared assets relative to the stylesheet, for example
`url("assets/background.png")`. Import rewrites those references into embedded
data URLs before normal preview/apply validation. No remote fetch, extraction to
arbitrary paths or CSS import is performed. Supported assets are PNG, JPEG, WebP,
WOFF and WOFF2; SVG, HTML, JavaScript and executable content are not package assets.
Keep unmodified upstream font files and their copyright/license notices together.
Pin source revisions and hashes when vendoring. Modified/subset fonts need a fresh
license review, including reserved font names. Different fonts must earn their
cost in rendered, realistic comparisons; do not bundle similar families merely
to increase choice.

The archive reader allows at most 4,000,000 ZIP bytes (5,333,336 base64 transport
characters), 128 members, 2,000,000 expanded bytes per member and 8,000,000 expanded
bytes total. Compression ratios above 200:1 are rejected. These limits are checked
before publishing anything. It rejects path traversal,
absolute/backslash paths, duplicate names, links, special files, encrypted members,
unsupported compression and corrupt streams. Runtime CSS retains the existing
1 MB limit; an otherwise small compressed ZIP can still exceed that limit after
embedding assets. Import is all-or-nothing and does not apply an appearance.
Version 1 accepts ordinary ZIP32 STORED/DEFLATE records, including signed or
unsigned data descriptors. ZIP64 and gaps between member records are unsupported.
Conflicting notice bodies at the same path fail rather than silently replacing
attribution. Package metadata in CSS exports carries complete retained notices.

Library refresh still reads and hashes each bounded ZIP. A service-local cache
retains at most 128 small validated summaries, keyed by exact archive-byte digest;
unchanged summaries avoid decompression and CSS compilation. Changed bytes and
explicit full reads validate again. This does not bound the total library count
or claim constant-time polling.

Read a saved package before editing or deleting it; its fingerprint protects the
canonical saved package, including assets and notices. Library edits preserve
package assets/notices. ZIP export preserves them, while CSS export remains
self-contained and carries required notices. Saving or applying does not execute
archive contents, change preferences or grant capabilities. Deleting the library
entry does not remove the already-applied CSS. Existing CSS-only imports and
default CSS export remain supported.
