# Current web client: first independent build boundary

This pilot exports today's HTTP/SSE client into a disposable, independently
buildable source capsule. It does not adopt the retained AHP client, move source
ownership, publish a component, or change bundled client/server behavior.
[CB](../../contracts/component-boundaries.v1.md) and
[CD](../../contracts/client-development.v1.md) govern the eventual extraction.

The existing build identity inventory selects authored frontend files, three
service catalogs, the shell SDK entrypoint, and official branding. The capsule
also carries their licenses, version metadata, the export tool, and a focused
fixture. Catalogs, SDK, and branding remain owned at their existing source paths;
the exported copies are finite build inputs with source hashes, not new owners.
There is no Python runtime or sibling checkout in the capsule. Dependencies use
the current npm lock. The existing default build still writes
`amplifier_web/static`; that relative layout inside the capsule is intentional
and temporary. Git-tracked static removal is a separate delivery transition.

From a checkout with the selected inputs committed:

```sh
node frontend/scripts/client-capsule.mjs export /tmp/web-source SERVER_COMMIT
cd /tmp/web-source/frontend
npm ci
npm run build
node scripts/client-capsule.mjs package /tmp/web-package
cd /tmp/web-package
npm pack --pack-destination /tmp
```

Use new, task-owned output paths. Application builds and browser qualification
must follow the workspace's DTU isolation rules. The export excludes generated
static, node_modules, Git history, local configuration, and backend source.
Uncommitted selected inputs fail explicitly; unrelated edits do not block it.

Install the resulting archive into a separate empty consumer with
`npm install --ignore-scripts --no-audit --no-fund /path/to/archive.tgz`.
Its only public code export is `webDirectory`, the absolute directory of built
assets; it has no runtime dependencies. `@amplifier/unified-client-web/manifest`
exposes the generated component record. The package is private and carries a
pilot version: this command does not publish or promise a supported release.

The single `component.json` record identifies the source revision, every exported
input, build identity, every packaged asset, and **one exact paired server
revision**. Its qualification starts as `unqualified`. A fixture pass does not
change that into actual-server or cross-version compatibility. Record acceptance
separately against this immutable record and actual peers; there is no runtime
compatibility registry, automatic fallback, or new negotiation layer.

The fixture consumer uses only the installed package plus client-owned test
helpers. It serves real HTTP/SSE and opens the built client in a browser:

```sh
cd /tmp/web-source/frontend
node tests/client-artifact-browser.mjs \
  /tmp/consumer/node_modules/@amplifier/unified-client-web SERVER_COMMIT
```

It checks rendered saved history, one accepted send, a response over SSE,
reconnect, reload, retained unfinished input, and no replay of accepted work.
It does not establish actual-server parity, native runtime/model behavior,
authority/cancellation parity, device support, or release adoption. A package
build and this fixture are the first bounded proof; qualification with the exact
paired server and ownership/distribution decisions remain before extraction.
