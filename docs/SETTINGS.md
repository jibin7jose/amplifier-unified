# Settings experience

Settings groups configuration into AI connections, Smart Tools, Appearance,
Workspaces, Voice, Notifications, Privacy & files, Testing & resources, Updates,
and Advanced. Wide layouts use a sidebar; below 960 pixels of available Settings
width, an index and one navigation level replace it. Presentation changes do not
migrate configuration or retire public destination IDs.

## Navigation and implementation owners

Use these owners for the current destination list and implementation rather than
copying an exhaustive route/component table into this guide:

| Responsibility | Owner |
| --- | --- |
| Section titles, destination IDs, parent routes and public aliases | [settings-navigation.js](../frontend/src/settings-navigation.js) and [navigation checks](../frontend/tests/settings-navigation.test.mjs) |
| Host routing of legacy destinations and Settings state for agents | [settings_navigation.py](../amplifier_web/settings_navigation.py) |
| Destination-to-editor mapping, generic contributed sections and retained editor mounts | [settings-experience.jsx](../frontend/src/settings-experience.jsx) |
| Compact hierarchy, browser history, scroll restoration and footer actions | [settings-mobile.js](../frontend/src/settings-mobile.js) and [settings-layout.jsx](../frontend/src/settings-layout.jsx) |
| Dirty-editor registration and review/discard decisions | [settings-drafts.jsx](../frontend/src/settings-drafts.jsx) |

`settingsSection`, `settingsExpanded` and `panel: appearance` remain supported
navigation inputs. Older Setup/Capabilities/Maintenance destinations resolve
through the route owner. Chat-specific aliases such as `conversation`, `runtime`,
`loaded-modules`, `share-bundle` and `recall` open their current chat panels;
`outputs` and `publishing` open the corresponding Canvas library view. Contributed Settings
sections appear generically under Advanced.

## Historical completeness evidence

The September 2026 [field inventory](settings-inventory.csv) retains 328
requirements across 25 feature areas: the original 321, six diagnosis/recovery
requirements and the Work summary detail preference. Its destinations and
validation entries describe that implementation, not today's route map or a
current release verdict. The later [Everyday Settings test-build record](validation/everyday-settings.md)
also keeps its own source identity, route counts and reported outcomes. Area
validation does not establish that every backend outcome was induced in a browser.
Use these records to preserve coverage intent, with current owners and new
exact-source evidence when behavior changes.

## Boundaries and state

- Unified-specific screens depend on integrations the app explicitly consumes. Loaded bundle modules are inspected and configured generically; their names never become hard-coded navigation entries. This follows [Amplifier repository rules](https://github.com/microsoft/amplifier/blob/main/docs/REPOSITORY_RULES.md).
- Diagnostics configures the app’s Context Intelligence client. Optional bundle hooks remain generic module configuration. File access edits the existing scoped write overrides through Unified’s shared policy API. The editor does not claim to display every effective restriction inherited from settings or bundles, and navigation does not add module-specific assumptions.
- Visited editors remain mounted while Settings stays open. Private provider keys and notification credentials remain in component memory until explicitly saved. Registered dirty editors offer review, discard or keeping Settings open before dismissal; closing never silently saves. Nonsecret editors use their existing view or local state, and the appearance editor has the separate persistence described below.
- Composer drafts are a different owner: the current host still stores acknowledged client drafts, while the browser journal retains unacknowledged edits. Ordinary model readback excludes those drafts. [CS2](../contracts/client-state.v1.md) and the [live-session contract](clients/live-sessions.md) distinguish current storage from the target client-local design.
- The shared activity and outside-dismissal contracts remain in effect. Pending receipts are distinct from completed background work, and navigation remains usable during independent operations.
- Existing skin tokens control color and typography. Structural layout supports widths down to 320 pixels and keyboard access. Compact Settings has one content scroll area between its Back/Close header and contextual actions.

## Validation

Follow the [release qualification policy](validation/release-qualification.md):
run affected unit and browser checks against exact source in an owned DTU with
synthetic state. Broad suites run nightly or by explicit request, not automatically
per release; this guide adds no exhaustive merge or publication gate. Maintained commands live in
[frontend/package.json](../frontend/package.json), including:

| Changed behavior | Relevant browser journey |
| --- | --- |
| Provider/module settings, sources and shared actions | `test:settings-browser` |
| Guided AI connections and everyday destinations | `test:settings-everyday-browser` |
| Credential isolation, notification/voice persistence and catalog-to-connection flow | `test:settings-completeness-browser` |
| Ordered collections, routing edits, pagination and partial-batch retry | `test:settings-collections-browser` |
| Compact navigation, Back/Forward, scroll and footer/keyboard layout | `test:settings-mobile-browser` |
| Appearance library, mutation lifecycle and preview boundaries | `test:appearance-library-browser`, `test:appearance-lifecycle-browser`, `test:appearance-preview-boundaries-browser` |

Choose checks from the actual changed owners, including backend contracts where
needed. Record fixture substitutions, source revision and actual results. Synthetic
catalog/provider checks do not establish live account, third-party package,
physical mobile keyboard or screen-reader acceptance. Historical layout counts
are not current coverage claims.

## Collections and ordering

Models, bundles, and Smart Tools use a compact collection with a focused detail panel. The full row, including its chevron and padding, is clickable; a checkbox remains a separate action. Narrow screens use the shared Settings Back header for collection details. Provider access is disclosed below the model and provider options. Nonsecret provider drafts survive switching selections, while unsaved private keys remain only in component memory.

Routing profiles display all task roles in a searchable collection, with only one role and candidate editor open. Provider family names and named connections remain distinct; catalog suggestions never restrict explicit model IDs or patterns. Unknown role, candidate, and profile fields survive ordinary edits and are available through JSON. Saving still affects future preparation, not a running session.

Provider priority, routing candidate preference, and bundle composition remain separate orders. Dedicated order screens support pointer/touch dragging, arrow keys, explicit up/down buttons, and a position selector. The lifted row follows the pointer, surrounding rows move, and a line marks the landing gap. Escape, pointer cancellation, or an outside drop restores the draft order. Dropping changes the draft. Provider and bundle Save order commits through their shared actions. Routing Save order updates the profile draft; Save active profile then commits that profile. If routing choices change while an order is unfinished, the order screen asks you to review the current choices before saving. Reduced-motion preferences disable movement animation. Provider and bundle order writes validate the complete original sequence to reject stale concurrent edits. Only enabled app capabilities enter composition order; disabled entries and standalone registrations remain intact.

## Model catalogs

Private catalogs persist across app restarts and are fresh for 24 hours. Configuration identity includes workspace, provider, source, endpoint, credential environment and credential-file content. Host-owned provider priority is excluded. Other provider options remain part of the key conservatively because arbitrary provider modules can interpret them. An expired catalog is shown immediately while an independent refresh runs; a failed refresh retains its last good values, with a retry cooldown. New configurations do not borrow a previous identity's models. Manual model/pattern entry remains available during discovery. Cache files are atomic, mode 0600, bounded in size, and contain digests rather than raw configuration keys. Tests still probe the saved provider independently of cached model discovery.

## Catalog batches

All matching entries are shown up to 50; larger results use pages of 40. Filtering searches the whole catalog. Selection persists across filters/pages, with page/all-matching/clear controls. Per-package extras are retained for the batch. `smartTools.installBatch` is the shared UI/agent action: it snapshots catalog sources, installs each package, persists individual progress, and retains partial successes. `retryOperationId` retries only unfinished items from that receipt. Restart interrupts work without replay. Installing remains separate from configuring and connecting an MCP server.

`providers.reorder` and `bundles.reorder` accept `ids` and `expectedIds`; the existing single-item move actions remain compatible. Ordering and catalog draft selections use the existing nonsecret editor view state.

The `test:settings-collections-browser` fixture covers unknown routing fields,
provider refresh during editing, order manipulation, private drafts across
selection, independent checkboxes, composition exclusions, catalog pagination,
partial installation and retry, and narrow layouts. It uses temporary storage
and an OS-assigned port without external package or account calls.


## Narrow screens and mobile

In compact layouts, opening Settings starts at its section index. A section replaces the index; details, routing choices and preference ordering replace their parent list. The header and browser Back follow the same hierarchy. Two local tabs remain visible; longer tab groups become a labelled section picker. Existing agent links still open their requested destination directly, with the complete parent path available through Back.

Visited editors stay mounted. Back restores list scroll and keeps filters, catalog selections, ordinary drafts and private provider/notification fields. Unfinished order drafts remain available on returning to the order screen; Cancel discards them and Save applies them through the existing action. Browser history stores only an opaque Settings token and depth, never form contents, credentials or configuration. Reload reuses those navigation entries without adding another history stack; private unsaved credentials remain memory-only and do not survive reload or closing Settings.

Primary provider, routing, module, catalog, connection, notification, permissions, diagnostics, registry, readiness and bundle-default actions move to a bottom action region. The actual controls are moved, with explicit form association for native submit buttons. Inactive editors cannot contribute footer actions. Safe-area padding and visual-viewport resizing keep that region above a soft keyboard; normal pinch zoom remains browser-controlled. Mobile inputs use 16px text, regular copy 14px, and controls provide 44–48px or larger touch targets.

The dedicated order screen retains the lifted drag preview, moving rows and between-item insertion line. Dragging starts only from the handle, so ordinary list touches scroll. A large position selector supplies a non-drag alternative. Catalog selection persists across details, filtering and pagination; the footer reports the total and the count outside the current page/filter. The existing all-through-50 / pages-of-40 behavior and per-package retry lifecycle are unchanged.

`test:settings-mobile-browser` exercises the built app against temporary synthetic
storage on an OS-assigned port. It covers destination access at several widths,
browser Back/Forward/reload, private draft isolation, nested routing, touch
dragging, unfinished order navigation, saved order, scroll restoration, catalog
selection, footer submission, a simulated keyboard viewport reduction and short
landscape layout. Consult the current fixture for its destination/viewport matrix;
these checks do not substitute for physical iOS/Android keyboard or screen-reader
acceptance.

## Appearance library

Appearance begins with selectable cards: Amplifier plus Aurora, Atelier, Graphite and Digital Rain. Every bundled appearance supports light, dark and device color modes. Aurora includes a flowing gradient background; Graphite has a subtle grid; Digital Rain has embedded emerald code rain with reduced-motion support. Background decoration is independently switchable. Ordinary appearance cards apply a shared choice immediately. Cards in **Save, import & export themes** preview locally in the attached client; that preview ends when leaving Appearance or closing Settings. Apply changes the shared appearance. Advanced CSS edits remain available below the library.

Upload validates and stores portable CSS in `<data-dir>/themes/*.amplifier.css` without applying it. Files added to that folder are discovered automatically while Appearance is open. Select an entry to **Edit**, **Export** or **Delete** it. The editor provides **Save as copy** and, for saved entries, **Update saved appearance**. Included entries are read-only. Delete requires a visible confirmation and removes only the library file; already-applied CSS stays intact.

The tab-private editor keeps the selected identity and fingerprint with its name, description and CSS. Another client's Apply cannot replace a dirty draft or mix its payload with the selected entry. Drafts survive visiting unrelated Settings sections, closing/reopening Settings and same-tab reload through browser session storage; failed browser persistence is explicit. Previews end on navigation or a committed appearance change and never resume from a saved editor draft. The renderer paints the exact validated preview snapshot. Portable imports accept LF/CRLF and an optional UTF-8 BOM. An older export can be reimported after an in-place update: an occupied content-based filename gets a deterministic unused suffix, never an overwrite.

Shared actions `theme.list`, `theme.read`, `theme.save`, `theme.import`, `theme.update`, `theme.delete` and `theme.export` provide the same lifecycle to agents. Update/delete use the exact fingerprint returned by the preceding read and reject stale edits. Polling never replaces an open editor's fingerprint or draft. Imports are atomic, identical portable uploads are idempotent, and changed uploads never silently overwrite another entry. Name, description, embedded assets and extra metadata round-trip in a single CSS file with one metadata header. Export by ID shares any saved or included entry without applying it; no-ID export still downloads the exact committed CSS. Invalid or externally dependent CSS, traversal IDs, links and nonregular files are rejected. Stored files survive app restarts. The theme folder is on the server host. See [theme lifecycle and portability](shell/themes.md#create-edit-delete-and-share) for the exact boundaries.
