# Appearances: interaction contract

See [Appearances](appearances.md) for the umbrella vision and authoring model.

An appearance is a coherent presentation of the whole application, not only a
palette. It covers surfaces, typography, density, spacing, borders, icons,
focus, interaction states, motion and the hierarchy of information. The host
owns behavior and accessibility; an appearance changes their presentation.

## Independent preferences

The web arrival experience additionally follows the [adaptive work views
contract](work-discovery.v1.md). Detail determines a default starting view;
explicit view selection and responsive layout remain independent.

- Theme: semantic colors, type, surface treatment and optional artwork. Use
  host tokens, including the contrasting on-accent foreground, rather than
  assuming that accent buttons always have white text.
- Scheme: light, dark or system. Every state must work in both palettes.
- Decorations: artwork on/off, independent of operational feedback.
- Detail/reveal: Focus through Everything changes how much supplementary
  information is shown. It never hides pending work, errors, required decisions,
  or the means to cancel/recover. Everything is not permission for more motion.
- Motion: honor the operating system's reduced-motion preference. The current
  host does not expose a separate global app motion preference. Static feedback
  must carry the same meaning; motion must never be the only signal.
- Contrast and input: forced colors, keyboard, touch, zoom and narrow screens
  remain usable regardless of appearance or detail level.

The theme definition, shell presentation preferences and operating-system
preferences are separate inputs. Do not silently write accessibility or detail
preferences when applying a theme.

## Interaction economy

Follow [CX8](../../contracts/client-experience.v1.md): UX is like golf—minimize
strokes to reach the intended result without overwhelming the interface. Prefer
visible selectors for small, commonly used option sets (for example, color
swatches); use disclosure when exposing everything would make choosing harder.
Reversible visual preferences apply immediately, with an unambiguous selected
state. Preserve deliberate submission for drafts, credentials, destructive
operations and external effects; fewer actions must not weaken authority.

Compare complete before/after journeys, including opening menus, applying choices
and repeated setup. Check keyboard access, focus, accessible labels and clarity,
not only pointer click counts. A compact layout must not hide required actions or
make the user memorize what unlabeled colors mean. Native clients use the current
platform controls and accessibility behavior; platform presentation remains the
client's responsibility rather than a browser imitation of a native widget.

Platform-specific implementation guidance belongs to its client owner. For an
example, see Tempo's [native Apple interaction contract](https://github.com/bkrabach/amplifier-temp/blob/feat/tempo-desktop-controls/docs/ux-contract.md).
It does not prescribe materials or controls for other clients.

## Waiting and saving

| Situation | Feedback | Interaction |
| --- | --- | --- |
| User starts an action | Initiating control looks unavailable; its existing icon becomes a compact busy indicator; use an action-specific label such as Saving… or Checking… where the owning component has lifecycle state | Prevent duplicate activation immediately; retain keyboard focus |
| Server accepted a long operation | Keep local status/label until the operation actually settles | Acknowledgement alone does not re-enable conflicting actions |
| Background refresh | Keep usable content silently; do not insert routine refresh text, live announcements, or layout shifts | Keep reading, navigation and unrelated editing available; initial loading and failures remain explicit |
| Save requires a lock | Explain Saving… outside the disabled fields; dim only affected controls | Use native disabled controls/fieldset for the precise write scope; preserve entered values |
| Failure or uncertain completion | Show a local explanation and supported retry/check-status action | Keep the draft; do not silently resend uncertain work |
| Successful completion | Restore the normal control; show a brief local confirmation when useful | Preserve focus and layout; do not flash the whole section |

`aria-busy` and `data-region-pending` express activity, not authorization to lock
an entire subtree. Do not derive `disabled`, `inert`, opacity or pointer blocking
for a region from these attributes. The owner of the save knows which inputs
conflict. Close, cancel, help and diagnostics remain accessible unless their
specific operation makes them unavailable.

For shared HTTP action feedback, buttons use `aria-disabled` plus captured
activation suppression so keyboard focus stays in place. Components continue
to own native `disabled` for validation and long operations. Native keyboard
activation produces the same guarded click as a pointer. Programmatic dispatch
and backend idempotency remain separate concerns.

## Visual rules

- No animated region background tint, traveling top-edge stripe, or page-wide
  pulse. Pending regions retain their authored background, border and shadow.
- Preserve the dimensions of existing icon slots. Labels and accessible names
  remain meaningful. Do not inject generic DOM text into React-owned labels.
- Busy icons inherit the control foreground; reduced motion uses a static broken
  ring with the same label/status. Forced colors uses system control colors.
- Use one clear local status, not a spinner on every child of a busy section.
- A save frame is an optional future treatment, not the default. If introduced,
  reuse the existing border or an inset overlay without changing box size;
  never obscure a focus ring. Its static version must be sufficient, and it
  must be tested across every appearance and motion/contrast preference.
- Essential feedback is the same at every detail level. More detailed modes
  may disclose phases, counts, timings and diagnostics behind disclosure.

## What is implemented and what still needs work

Existing theme schemas cover complete light/dark palettes, artwork and portable
CSS; shell preferences cover presentation, and Canvas apps receive host color,
contrast and motion context. See [themes](themes.md) and
[conversation surfaces](conversation-surfaces.md).

Shared action feedback supplies immediate busy semantics, duplicate-click
suppression and an in-place icon indicator. Activity regions retain lifecycle
semantics without visual surface effects. Components own working labels,
long-operation lifetime and native input locking. Text-only controls need a
component-owned working label or adjacent status; the shared layer does not
fabricate one or assume an HTTP receipt completes the job.

This contract is the design baseline, not a claim that all existing controls
conform. Remaining work is to inventory text-only actions and save scopes,
standardize component-owned labels/locking, and extend automated appearance
coverage across all surfaces. Custom CSS remains capable of overriding host
styles; schema validation alone does not prove visual/accessibility compliance.
A richer versioned interaction-token schema and an app-level motion preference
are future work, not options the current UI already offers.

## Acceptance

### Chat Plan placement

`presentation.planPlacement` accepts `composer` (the default when absent) or
`inline`. Settings → Appearance → Plan location exposes these as **Above
composer** and **Inline in chat**. It uses the existing saved presentation
protocol, including agent-driven presentation changes. Changing placement must
not change the plan, execute work, or reset the conversation.

Both placements consume one read-only projection of the latest successful todo
receipt for the selected chat. Child-chat plans do not replace a root's plan.
Canonical event logs own the reports; the activity page and loaded transcript
window do not determine whether the Plan is available. The projection can be
rebuilt after restart and is not an independently editable task store.

Show the reported completion count and the current step, with an expandable
checklist above the composer or an initially expanded checklist in chat. A
reported in-progress step is only shown as working when its owning turn is
currently active. Paused/failed work remains unfinished. Completed checklist
items are agent reports, not proof of an output, test, publication or release.
An empty reported list clears the Plan. Unreadable or oversized current reports
show an unavailable state rather than substituting an older plan.

Essential Plan information remains available at every Reveal level. Existing
appearance tokens own colors and spacing; the first implementation uses no
animation, so reduced motion does not lose status information.

Hold requests and long-operation completions independently. Check immediate
feedback, duplicate pointer/keyboard suppression, failure cleanup, draft
preservation and dismissal. Compare pre/pending/post geometry. Confirm a
refresh does not disable unrelated fields or alter its parent surface.

Exercise light/dark/custom themes, decoration on/off, Focus/Balanced/Everything,
normal/reduced motion, forced colors and narrow layouts. Check readable status,
visible focus, accessible names and live announcements. Verify actual busy
states; static theme thumbnails and screenshots alone are insufficient.

### Transcript overflow cues

The shared scroll owner reports content above and below the transcript viewport.
An edge fade is a static overflow cue, not background artwork or work status.
Use it only where content continues beyond that edge. At the end, the bottom
edge must be fully opaque so final text and message actions remain readable.
Short content must not fade. Preserve the existing reading anchor, follow,
Latest-button and chat-return behavior.

Mask only the transcript, not its composer, navigator or Latest control. Preserve
the measured scrollbar gutter. Suppress the mask when the scroll pane or a
descendant has keyboard-visible focus, or when an `input`, `textarea`, `select`,
`[contenteditable]`, or `iframe` is focused. This keeps focus feedback and active
editing readable without disabling fades for pointer/programmatic focus on a
non-visible container, such as the question section focused by **Review questions**.
For embedded browsing contexts, the host document's active `iframe` inside the
transcript is authoritative even if CSS `:focus` and `:focus-within` are false.
The shared scroll owner observes native host focus changes without reading the
child document, moving focus, or changing scrolling. A mounted but inactive frame
must not disable fades. Leaving or removing the frame clears the host guard.
Do not remove native focus indicators. Forced colors removes the mask.
Decorations-off does not remove this functional cue. Appearances may tune
documented fade lengths, but should not replace the mask, add scroll listeners or
reposition content to simulate overflow.

### Appearance editing operations

Styling packages use an ordinary ZIP with a root `appearance.json` declaring
`type: "appearance"` and `formatVersion: 1`. Import validates the entire archive
before any library write. Failed import preserves saved entries, selected
appearance, client drafts and preferences. Import never applies the result.
The version-1 manifest contains a stylesheet, optional attribution and declared
image/font assets and license notices only. It cannot install code, components,
layouts, capabilities, accounts or preferences. Unsupported versions and members
fail explicitly; there is no generic archive execution or external asset fetch.

The bundled `theme.import` action accepts exactly one of `css` or
`packageBase64`. `theme.export` keeps CSS as its default and accepts explicit
`format: "zip"` for a package download. An `id` selects a library version;
omitting it exports committed styling, never a preview or editor draft.
Version 1 accepts PNG/JPEG/WebP images, WOFF/WOFF2 fonts and UTF-8 text notices.
Font assets require a declared bundled license reference.

Bound decode, expanded member sizes, total expanded bytes and member count.
Reject traversal, ambiguous names, links, special/encrypted members, corrupt
streams and unsupported compression/types. Runtime CSS remains self-contained
and subject to the existing validator and size limit. Package update/export
retains assets and notices; stale fingerprints reject mutation of a changed
package. CSS-only workflows retain their established semantics. License presence
and metadata validation are not legal-rights certification.

Saving, previewing, applying, importing, exporting and restoring an appearance
report their current operation locally. Text-only editor actions use working
labels in an unchanged button slot. Name/CSS fields and conflicting actions are
unavailable during the operation; navigation and closing remain available.
A failed save retains the draft and restores editing. Completing a save while
its page is inactive must clear the local lock and retain any failure explanation
for when the user returns.

The production-host `appearance-save-feedback-browser.mjs` delays real save
requests and covers keyboard duplicate suppression, locked fields, stable button
width, local status, failure/retry, inactive completion, light/dark, reduced
motion, forced colors and mobile layout. It uses synthetic temporary storage
and makes no model requests. This is additional coverage for the appearance
editor, not evidence that all other settings surfaces conform.

### Credential saving and file selection

Saving a voice connection locks its source and key fields until the receipt
settles. Clear the entered key only after confirmed acceptance; rejection or a
missing receipt retains it for retry. The credential draft remains local to
the component. Closing Settings and unrelated settings remain available.

Shared file selectors display an operation-specific working label and disable
selection while processing. Conversation import and appearance upload use the
same control; repeated drops must not start concurrent processing. Working
labels share the appearance editor's fixed-size label slot without introducing
motion or changing region surfaces.

`settings-save-feedback-browser.mjs` exercises delayed production requests,
synthetic credential storage, rejected save/retry, precise field locking,
duplicate keyboard activation, file upload, stable label geometry, light/dark,
reduced motion, forced colors and narrow layout. It makes no model calls.

### Folder browsing

Folder lookups show an immediate working label and a local loading status even
when a prior listing is already visible. Keep the prior results readable. Lock
the lookup path and competing folder-navigation controls while that lookup is
pending; Close and unrelated fields remain available. Failed lookups retain the
path and list and offer retry. The working label keeps the same button width,
and activity does not change the listing surface's background, border or shadow.

`location-feedback-browser.mjs` checks delayed production requests, repeated
keyboard activation, failure/retry and closing during a lookup. It applies actual
light/dark and Reveal preferences through the same shell protocol available to
agents, checks reduced motion and forced colors, and uses synthetic directories
without model calls.

### Saving file access settings

Saving locks the selected scope and its allowed/denied paths, including folder
pickers, until the request settles. The save button uses a stable-width working
label and a nearby status; the scoped fields dim without adding a border or
animating their background. Panel dismissal stays available. A rejected request
keeps both path drafts and the chosen scope for correction and retry; it must
not display an older successful-save message as the current result.

`permissions-feedback-browser.mjs` delays and rejects a production request,
checks field and duplicate-activation guards, and verifies that a corrected
retry persists to the selected synthetic scope. It also checks actual light/dark
and Reveal settings, reduced motion, forced colors, and the compact action
footer. The fixture sends no model input.

### Shared surface corners

The conversation, composer, navigation rail, dialogs and artifact frames use the
appearance's `--a-radius`. Built-in presets define that token for both color
schemes. Full-screen surfaces retain square outer edges; circular status and send
controls retain their functional shapes. Exact unmodified stock skins from .93
upgrade by content hash. Edited custom CSS remains unchanged.

`surface-radius-browser.mjs` checks computed corners for every bundled appearance
and both schemes, narrow layouts, forced colors, reduced motion and draft retention.

### Execution metrics

`presentation.executionVisibility` has optional boolean `step`, `turn` and
`conversation` entries. An explicit entry overrides Reveal for that scope;
absence retains its default. `executionMetrics` is an allowlisted array of
`elapsedSeconds`, `grossInputTokens`, `outputTokens`, `grossTotalTokens`,
`cacheReadTokens`, `cacheWriteTokens`, `reasoningTokens`, `costUsd` and `calls`.
Omission selects all supported fields; an empty array selects none. Updates
replace these values using the existing revision-bound presentation protocol.
UI edits preserve the other scope entries. `conversationStatus` enables the
optional compact, demand-refreshed status display and defaults to false.

These are display preferences. They never hide required decisions or stop/error
state, change execution, widen authority or reinterpret accounting. Known,
partial, estimated, live/pending and unavailable values stay distinguishable.
Exports include selected accounting fields and bounded identity, never reasoning
text or request/response captures. Missing context-window, queue and internal
retry telemetry is not fabricated. Desktop and narrow layouts must wrap, and
hidden conversation totals must not initiate an accounting read.


The quiet background-refresh rule follows the [October 20 UX decision](../ux/decisions/20261020.md).
User-initiated saving, execution progress, and actionable failures keep their existing feedback.
