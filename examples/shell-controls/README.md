# Independent shell contribution

This example imports only the public SDK and uses host React. It adds compact
spacing controls, a conversation-status widget, or a custom Settings section.
It is an authoring example, not a default installed component.

Copy this directory to a separate project. With Node.js 18 or newer and npm,
install a built SDK tarball and build; no Unified checkout or frontend install
is needed. Until the SDK has a registry release, use the explicit tarball path:

```sh
npm install /path/to/amplifier-shell-sdk-1.0.0.tgz --save-exact
npm run build
```

This creates `dist/module.mjs`, `dist/manifest.json`, and a build-input report.
Commit the generated lockfile in your own component project. React comes from
the host and must not be added to this bundle.

Unified contributors can produce that tarball with `npm pack` in
`packages/shell-sdk`. The existing frontend-toolchain entrypoint remains available
for in-repository tests:

```sh
node frontend/scripts/build-shell-controls-example.mjs
```

Stage `dist/manifest.json` and `dist/module.mjs` with `shell.packages.stage`, run
host validation, then add an instance to one of its declared slots using the
existing composition preview/apply flow. For `app.actions`, explicitly include
`builtin.app-actions` too if you want to keep the standard header buttons.

The Settings instance demonstrates durable module state and a pending edit
that blocks replacement. Its state does not modify the conversation draft.
See [the component contract](https://github.com/microsoft/amplifier-unified/blob/main/docs/shell/components.md) for capability,
client targeting, generation and native-code trust boundaries.
