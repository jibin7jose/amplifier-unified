# Shell component contributions

`shell.inspect` publishes the installed host's `slots`, `registry`,
`resolvedInstances` and `componentCommands` argument schemas. Discover them
before authoring; do not guess slots from component filenames. Navigation and
artifact viewers retain their existing profiles and SDKs.

The `trusted-native-component-v1` profile contributes to:

| Slot | Limit | Built-in fallback |
| --- | ---: | --- |
| `app.actions` | 8 | App/header controls |
| `app.status` | 8 | Connection status |
| `conversation.header` | 1 | Conversation selector |
| `composer.actions` | 8 | Notification/model/bundle controls |
| `canvas.toolbar` | 8 | Canvas controls |
| `settings.appearance` | 1 | Appearance editor |
| `settings.section` | 12 | None; each instance adds a named Settings section |

A composition still contains `instances` and `presentation`. If a slot has no
explicit instance, the host inherits its registered built-in. Existing saved
compositions therefore need no rewrite. `resolvedInstances` includes those
inherited instances and their stable `core.*` IDs. Add explicit instances to
replace a slot; include its built-in package too if the new controls should
supplement it. Use `disabledSlots` to intentionally hide a supported slot.
A slot cannot be disabled and contain explicit instances simultaneously.

Custom instances retain composition order. In app actions/status, built-ins
stay first; additional contributions share an overflow group on narrow screens.
The same mounted controls stay in that group as the viewport changes. All
custom controls still need accessible labels, keyboard operation, readable
styles using shell color tokens, and layouts that fit the available space.

## Package contract

A manifest requires `id`, `label`, `version`, `apiVersion: "1.0"`,
`profile: "trusted-native-component-v1"`, `stateSchema`, `slots`, and
`capabilities`. `slots` enumerates compatible destinations. Its default export
is a factory `({React}) => Component`; the component receives `{host}`. Use host
React and the public `useShellComponent(React, host)` helper. Bundle dependencies
into one ESM artifact; do not import private frontend implementation files.
See [the independent example](../../examples/shell-controls/README.md).

On Linux/macOS, check the package boundary with `node scripts/qualify_shell_sdk.mjs`. It packs
the SDK, installs it into a disposable consumer outside the checkout, builds the
actual controls example and checks that its only bundled dependency is the SDK.
The resulting `output/shell-sdk-proof` holds the tarball, component and receipt;
temporary dependencies are removed. This is a package check, not host acceptance.
Inside an owned test environment, set `AMPLIFIER_SHELL_COMPONENT_PACKAGE` to that
output directory when running `tests/web/shell-components-browser.mjs` to
exercise the same installed artifact through real host validation, preview/apply,
dirty-edit deferral, stale callbacks and recovery. The fixture also rejects an
unsupported API revision. Registry publication and repository extraction remain
separate delivery steps.

This profile remains **trusted same-origin code**. Capability checks constrain
SDK operations, not arbitrary native JavaScript. Compatibility validation is
not a security audit or an isolation boundary. Do not load untrusted code with
this profile. Sandboxed conversation Canvas Apps remain a separate contract.

## Data and actions

Every component snapshot contains an app revision, composition revision,
instance generation, slot, and its own durable `view` object. `shell.read` is
required and supplies presentation, selected IDs and runtime availability.
Optional `conversation.summary`, `canvas.summary` and `attention.summary`
supply only their bounded summaries. They do not expose messages, streaming
text, composer drafts, artifact bodies, mount plans or credentials.

`host.subscribe` shares the existing shell reconciliation and returns cleanup.
Use `useShellComponent` for a stable, immutable snapshot. No second event stream
or model turn is started by a component edit. Agent reads remain explicit.

The bundled browser uses [scoped shell resources](../browser-state-transport.md#shell-bootstrap-and-active-resources).
It bootstraps descriptors once, then receives changed requested sections through
the existing state stream. Hidden reading surfaces do not construct their pages
merely to retain controller state. Custom components still receive their complete
contracted snapshot through the SDK; explicit `shell.query` and `shell.inspect`
reads retain their contracts. A view losing read interest does not cancel accepted
commands, change instance generation or discard dirty drafts and Canvas inputs.

`host.dispatch('view.update', {patch})` patches only the instance's UI state;
accumulated state is limited to 16 KB. It cannot change a conversation draft.
Other supported commands and required capabilities are discoverable in
`shell.inspect.componentCommands`:

- `panel.open` (`panels.open`): Settings, Activity, Chat details or Chat controls.
  For a contributed Settings section, include its instance ID as `section`.
- `presentation.update` (`presentation.update`): a `patch` and observed
  `expectedRevision`, through the existing prepare/apply path.
- `session.rename`, `session.naming`, `conversation.stop`
  (`conversation.manage`): explicit selected session ID required. A selection
  change rejects the old target rather than acting on the new conversation.

For agent-visible controls, annotate buttons with `data-action="shell.command"`
and `data-shell-command` naming the semantic command. Visible UI observations
include the containing component instance ID and slot. Commands remain
validated against the manifest and the installed command schema.

Agents use `shell.query {clientId,instanceId}`, then `shell.command` with the
observed `generation`, action and arguments. The browser SDK supplies that
generation automatically. Replaced instances reject late writes and commands;
retry an uncertain admitted command with its original receipt ID and payload.
Unchanged components retain their generation across appearance changes.

## Activation and preservation

Use the existing stage, host validation, prepare, preview, apply, report, revert
and recovery flow. Validation exercises empty/populated snapshots for every
declared slot, repeated mount/unmount and subscription cleanup. Browser
activation evidence is separate; open contributed Settings pages to exercise
their components before calling the composition verified. An inactive component
has not yet supplied mounting evidence. Previously verified mounted components
retain their activation evidence when a containing panel closes.

Declare unsaved edits with `await host.setDirty(true)` and clear the flag only
when saved or canceled. Dirty removal/replacement defers, and incompatible state
schemas reject replacement. Compatible replacements preserve durable view
state; arbitrary React local state is not migrated. Default/recovery rendering
contains failed optional components, and `?shell=recovery` bypasses them.

These outlets preserve their surrounding conversation, composer and canvas
viewer nodes. They do not yet replace the whole conversation pane, composer,
message/execution renderers, canvas container, voice implementation, or Settings
system. Layout packages and isolated shell execution need their own contracts;
none should be presented as available APIs on this host.

Run `npm run test:shell-components-browser --prefix tests/web` against a production
build. The disposable provider-free proof builds and validates a separate module
after opening the app, uses user and agent actions, checks dirty/stale behavior,
retains the actual composer/iframe nodes, and verifies draft/module-state reload
and recovery. This is not a live deployment or real-provider validation claim.
