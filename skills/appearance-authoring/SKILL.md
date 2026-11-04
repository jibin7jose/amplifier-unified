---
name: appearance-authoring
description: "Create, revise, or package Amplifier Unified themes and CSS appearances with light/dark support and safe preview/revert. Load for custom appearance authoring, not for simply choosing an existing appearance. Shell component/layout authoring belongs to amplifier-shell."
---

# Author an appearance

Work inline so the user can review and steer. An appearance covers the whole
application, not just its palette. The host owns behavior, accessibility, drafts,
conversation data and action semantics.

Resolve resources from the `skill_directory` returned by `load_skill`, not the
working directory. Read [examples.md](examples.md) for payloads, selector lessons
and a small acceptance run. Canonical references, relative to this directory:
- [Appearance scope](../../docs/shell/appearances.md)
- [Theme API and CSS tokens](../../docs/shell/themes.md)
- [Interaction contract](../../docs/shell/appearance-contract.md)

## 1. Inspect and bound the change

Read `app_control(operation="get_state")`; identify the intended connected client
from current input/visible UI, its appearance, and relevant shell presentation.
Discover `theme.*` and, only for layout/presentation changes, `shell.*` with
`list_actions`. Follow `nextOffset` at the same catalog revision. For oversized
schemas, follow `schemaRead` and assemble all schema pages before dispatch.

Use `theme.list` and `theme.read` to retain the exact starting CSS, saved id and
fingerprint, plus the committed appearance and revert path. Read targeted CSS
in bounded pages: embedded images can make a stylesheet exceed tool limits.
Never reconstruct a truncated stylesheet. State the affected surfaces and files.
Read the installed baseline styles as well as the saved skin. Check the actual
DOM and computed styles before adding an override; a skin can duplicate or defeat
a shared primitive even when its own CSS looks correct.
Use supported theme actions, not filesystem settings or database edits. Preserve
unrelated Canvas work, drafts, bundles, providers, skill budgets and exclusions.
Offline authoring is allowed; label activation and browser review unverified.

**Success criteria:** Explicit target, current schemas, starting state and scope.

## 2. Author the smallest coherent treatment

Choose exactly one theme input:
- `definition`: a new version-1 theme with all ten colors in both palettes;
  starts from the built-in skin rather than inheriting earlier artwork.
- `tokens`: an intentional partial palette edit retaining the existing skin
  and background; structured themes update both palettes.
- `css`: a complete portable skin, including light/dark styling and optional
  shape, spacing and panel tokens from the installed theme contract.

Use semantic host tokens, including the contrasting on-accent foreground. Keep
text readable, focus visible and touch targets usable. Prefer documented tokens
over brittle DOM selectors. Scope CSS to `#amp-one` and stable `data-part`
attributes. Inspect the actual DOM before font, bubble or layout overrides;
inherited styles may lose to paragraph/span rules. Preserve native conversation
layout and positioning. See the companion's failed-selector examples.

Style bubble bodies, not outer message rows or their action footers. The inner
composer surface is separate from its form, model selector and status. Keep
artwork lines away from controls. Optional logo filters must reset in forced
colors with selectors at least as specific as every light/dark filter rule.

Let the host own transcript overflow cues, focus handling and scrollbar clearance.
Keyboard-visible focus and active editing controls keep the transcript unmasked;
pointer/programmatic focus on a non-visible container alone does not disable its
fades. Omit fade tokens to inherit the shared defaults; use documented optional
tokens only after checking installed support. Do not add unconditional masks,
overlay fades, scroll listeners or child-positioning workarounds. Keep background
artwork separate from the transcript mask.

Use neutral, IP-independent names and only authorized, self-contained assets.
For a reference-inspired appearance, ground both schemes in the reference.
Keep the shared design language but choose materials and colors independently:
do not make light mode a generic pastel inversion of a carefully authored dark
mode. Record which details are observed and which are readability adaptations.
Prefer system font stacks. Backgrounds may use accepted gradients or embedded
PNG/JPEG/WebP data URLs, never external URLs, imports or SVG data URLs. Do not
copy a brand's name, logo or proprietary artwork into a portable appearance.
For multiple assets, use the versioned ZIP contract in the linked theme guide:
root `appearance.json`, local CSS, declared images/WOFF fonts and license notices.
The ZIP extension is not proof of type or safety. Retain author metadata and
original notices on export. Compare realistic rendered specimens before adding
a font family; avoid several similar families without a useful distinct role.

Do not change scheme, decorations, Reveal, reduced motion, contrast or permissions
as a side effect of a theme. Busy regions must not pulse, tint or lock as a whole;
preserve local status, errors, decisions, cancel and recovery. Components retain
their host-owned pending/disabled semantics.

For supported composition changes, use `amplifier-shell` and the discovered
prepare/preview/apply/revert protocol; preserve unaffected instance IDs. The
styling ZIP does not bundle composition, executable components or arbitrary
composer replacement.

**Success criteria:** Complete requested treatment with independent preferences
and operational feedback preserved.

## 3. Preview and check the real result

Dispatch through `app_control(operation="dispatch", args={"action": ..., "args":
{...}})` using the exact installed schema. Preview on the intended client before
committing. Theme preview is client-scoped; applying commits the app-wide skin.
Saving to the library does not apply it. Do not change unrelated clients'
presentation or conversation selection.

Check the exact proposed revision in the real browser: light/dark/system,
decorations on/off, reduced motion, forced colors, narrow layout and zoom.
Exercise keyboard focus, long chats, code, local busy/error states and required actions.
Verify unsent drafts and dirty viewers remain intact without reading private
draft content unless requested. Use disposable fixtures or authorized temporary
preferences; restore those preferences after testing.

**Success criteria:** Observed rendering and interactions match the proposal;
schema acceptance, thumbnails and pending browser receipts are not visual proof.

## 4. Save, share and retain recovery

`theme.save` creates a copy; `theme.import` retains portable CSS or styling-ZIP metadata without
applying or silently replacing an entry. `theme.update` and `theme.delete` use
a saved id and fresh `expectedFingerprint` from `theme.read`; included appearances
are read-only, so save a custom copy. Confirm destructive deletion with the user.
Library updates/deletion do not change committed CSS. Export by id to share the
saved version; export without id means the committed appearance, not a preview
or editor draft. Round-trip the exported file through import before claiming
portability. Use installed schemas; do not guess payload fields.

For an authorized bundled addition, use the source-controlled stylesheet and
metadata, not a migration of saved user copies. Lightline is the static
architectural example; included does not mean changing anyone's selected skin
or independent preferences. Keep packaging checks focused on the changed skin,
shared primitives and actual host controls, not a new broad release gate.

Apply only within the user's request. Reinspect committed state and browser
status; check preview/apply parity, reload and revert when a safe test permits.
Honor revision/dirty-edit guards. For uncertain effects inspect the retained
receipt and current state; never resend under a new identity. A stale proposal
needs fresh inspection, not a forced revision.

Report the saved deliverable, applied scope, observed checks, remaining limits
and usable revert path. Do not claim application or visual QA for an offline file.

**Success criteria:** Verified requested state, or an explicitly offline artifact;
unrelated settings and host-owned data remain unchanged.