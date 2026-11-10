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

## Qualify the actual paired server

The next integration check uses the real server's auth, actions, history and SSE,
with the existing synthetic chat runtime and provider fixtures. Install the static
npm archive in a separate consumer. In an owned copy of its exact paired server
revision, replace `amplifier_web/static` with those installed static bytes before
starting the server. This preserves the single path used by HTTP serving,
installed build identity and shell validation; no runtime asset-directory option
or compatibility registry is introduced. It is a composed source server, not an
unmodified published wheel or release qualification.

Capture a finite assembly record outside the source tree:

- `server`: exact `revision`, actual `version`, and `files` mapping relative
  server/resource/fixture paths to `{sha256, bytes}`, captured from that source
  revision before composition; exclude `amplifier_web/static`.
- `client`: the installed archive's SHA-256 in `archiveSha256` and its unchanged
  `component.json` in `manifest`.

The record is evidence supplied by the integration owner, not application state.
The fixture checks source and installed static bytes before startup and after
shutdown, and verifies the actual imported server/version and declared pairing.
Keep the independent artifact and previous qualification records immutable.

```sh
AMPLIFIER_TEST_PYTHON=/path/to/owned/python \
node frontend/tests/client-paired-server-browser.mjs \
  /path/to/owned/server-source /path/to/assembly.json /path/to/owned/state-parent
```

The test rejects missing/invalid credentials, checks served entry/build bytes,
sends two messages through real admission, reloads saved history, preserves an
unfinished draft, and checks that reconnect/reload do not repeat accepted work.
Its authenticated, loopback-only fixture endpoint closes the selected client's
actual SSE transport. Native EventSource retry can reuse its JavaScript object,
so the check observes a new open event, fresh baseline and subsequent delta.
Only the transport advertisement is narrowed to select SSE; build and server
identity responses are unchanged. Providers/runtime remain synthetic, and
background updates/work and provider preloading are disabled. This does not
qualify native execution, model accounts, devices, other versions or AHP.

## Shared inputs for the source ownership transfer

The client repository is [microsoft/amplifier-client-web](https://github.com/microsoft/amplifier-client-web),
following the existing `amplifier-client-macos` naming convention. Its initial
history import preserves `LICENSE` and `frontend/` from Unified 0.70.0
(`4854dbd9ec555690269747b7dbe273f008e9e179`). Independent build preparation and
qualification precede the source transfer; Unified remains the canonical source
for the shipped client until that coordinated handoff. This prerequisite does
not publish another SDK or adopt the retained AHP client.

| Source or check | Owner at the transfer boundary |
| --- | --- |
| Authored `frontend/src`, `frontend/public`, client build and packaging | Web client; Unified retains the current source until the coordinated transfer |
| Client-only unit tests and standalone HTTP/SSE fixture | Web client, classified by actual dependencies before moving |
| Real AppService fixtures, installed composition and server-dependent browser checks | Unified integration owner; retain their coverage and repoint their client input |
| Three shared JSON catalogs, official branding and current shell SDK | Existing canonical paths in Unified; generated build copies are never edited |
| Family/client contracts and tested default selection | Existing contract and distribution owners; reference their promises rather than copying them |

Do not move the entire frontend test directory by assumption. Some checks read
server source or start Python fixtures; these need an explicit owner and installed
peer entrypoint. The independently packaged client remains paired with one exact
server revision until its compatibility and adoption policy is deliberately
expanded. Installation continues to assemble assets into `amplifier_web/static`,
preserving serving, installed build identity and shell validation. Released
packages remain self-contained; development builds require Node.

The existing capsule tool can export just the finite shared inputs:

```sh
node frontend/scripts/client-capsule.mjs inputs /tmp/web-build-inputs
node frontend/scripts/client-capsule.mjs verify-inputs /tmp/web-build-inputs SOURCE_COMMIT
```

The selection comes from the existing build inventory outside `frontend/`, plus
the full capsule's existing version metadata and licenses. It currently contains
15 files: the three catalogs, SDK entrypoint/package/license, six branding files
and their upstream license, root license, and `pyproject.toml`. The current build
reads its version from `pyproject.toml`; a later independent client version must
replace that dependency explicitly. Dependencies under `frontend/node_modules`
remain selected by the client's npm lock and are not exported.

`build-inputs.lock.json` records the canonical input-owner repository/commit and
each relative path, byte count and SHA-256. It is generated data. It contains no
paired-server qualification, runtime registry or new catalog definition. The
client can pin that lock and receive the same bounded payload online or as an
offline archive, keeping materialized copies under an ignored build directory.
Its importer and independent release version are the next client-owned batch.

Verification is offline and checks the selected revision, exact file membership
and byte hashes; missing, changed, extra or linked files fail. The selected lock
must come from a trusted canonical export, or its archive/digest must be pinned
by the component selection. A matching self-contained lock does **not**
authenticate source provenance if somebody replaces both files and lock. No
GitHub download or alternate SDK publication is introduced here.

Use new task-owned export paths. Retain the small lock and useful qualification
record; remove superseded materialized inputs with their build task. The current
capsule commands, build identity and generated application output are unchanged.
