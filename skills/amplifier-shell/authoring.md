# Author and hot-load a component

Read the matching sections of [the shell guide](../../docs/shell/README.md)
and [SDK types](../../packages/shell-sdk/index.d.ts). Start from:

- [Navigation example](../../examples/shell-navigator/index.js) and
  [manifest](../../examples/shell-navigator/manifest.json).
- [Header/status/Settings controls example](../../examples/shell-controls/index.js)
  and [manifest](../../examples/shell-controls/manifest.json).
- [Artifact reader example](../../examples/shell-reader/index.js) and
  [manifest](../../examples/shell-reader/manifest.json).

1. Choose the supported navigation, component, or renderer profile. Define a stable module
   ID, semantic version, state schema and only the capabilities it uses.
   Renderers also declare supported resource kinds. Components declare supported
   slots from the installed host catalog.
2. Implement the default `({React}) => Component` factory with `{host}` props.
   Use host-supplied React and the public SDK. Do not import private app stores,
   replace canonical conversation state, or bundle another React instance.
   Clean up subscriptions on unmount. Persist supported view state through the
   host and mark unsaved edits dirty; arbitrary component-local state does not
   migrate automatically.
3. Compile one self-contained ESM artifact within the host's size limit. In a
   source checkout the example build scripts are under `scripts/examples/`.
   Installed SDK/examples can also be built with an external compatible
   toolchain. Keep compiler output separate from installed reference files.
4. Stage the manifest and exact compiled source with `shell.packages.stage`.
   Validate its digest with `shell.packages.validate`. Fix failures and restage
   changed bytes. A missing validator toolchain is a blocker to activation,
   not grounds to fabricate approval. See the guide for
   `AMPLIFIER_SHELL_NODE_MODULES`, Node, Rollup, and Playwright requirements.
5. For navigation or component slots, inspect, prepare a composition using the validated package,
   preview, then apply. For a renderer, inspect the canvas view and switch its
   renderer using the current target and validated package. Follow the live
   action schemas for payloads.
6. Verify browser reports for this exact revision/package, then exercise empty
   and populated states, interaction, cleanup, reload, dirty deferral, and
   revert/recovery. Include preservation of unsent drafts and conversations.

**Success criteria:** The host validates the exact artifact, the intended live
client mounts and uses it, and preservation/recovery checks pass. Report only
the stages actually verified.

Native profiles run trusted same-origin code. Compatibility validation and
capability checks are not a sandbox or a security review. Work within the
user's authorized scope, and do not activate untrusted third-party code under
an assumption that the host isolates it.

## Header, status, composer controls, canvas tools and Settings

Read [component contributions](../../docs/shell/components.md) and discover the
running host's `shell.inspect.slots` and `componentCommands`. Use
`trusted-native-component-v1` and `useShellComponent` for those declared slots.
Follow the generation returned by `shell.query`; late actions from replaced
components are rejected. Do not expose full conversation state to a summary
widget or claim this trusted native profile isolates untrusted code.
