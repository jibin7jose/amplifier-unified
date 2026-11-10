# Web client ownership and assembly

The [Amplifier web client](https://github.com/microsoft/amplifier-client-web) owns
browser source, client tests, production dependencies and its build tools.
While that repository awaits public availability, [`web-client/`](web-client/)
supplies its selected source here. [`web-client.source.json`](web-client.source.json)
selects the canonical commit, source digest and delivery location. Author changes
in the client repository and refresh the whole snapshot; this copy is not a second
place to develop the client.

Unified owns `scripts/web_client_build.py`, canonical shared inputs, package
assembly and [`tests/web`](../tests/web/README.md). The latter contains host,
browser-integration and input-producer checks with an explicit test dependency
lock. Client-only checks remain with the client. Shared generic test helpers have
one implementation; each owner injects its test-framework facade where needed.

## Prepare, test and build

Run from the Unified root in the owned development environment:

```sh
python scripts/web_client_build.py prepare
npm ci --prefix .build/web-client
npm ci --prefix tests/web
npm test --prefix .build/web-client
npm test --prefix tests/web
python scripts/web_client_build.py build
npm run test:artifact-assets --prefix .build/web-client
python scripts/web_client_build.py verify
```

These commands have separate scopes: client units, host integration/producer
units, production build, built-asset checks and installed-asset provenance.
Select affected checks under the existing release policy; this is not a new
mandatory full-suite release gate. Browser journeys use the registries described
in [tests/web/README.md](../tests/web/README.md), with owned Python fixtures and
matching Chromium where required.

Preparation creates ignored `.build/web-client` from the exact selection,
including its own package, lock and tests. It verifies current catalogs, SDK and
branding against the selected shared-input lock offline. The production build
uses that client's tools and installs its artifact in `amplifier_web/static`.
Reuse dependencies when their locks and toolchain match. Repository delivery
requires exact source preparation before a managed source snapshot is transferred;
missing preparation is an error, not a fallback to another client selection.

The client has an independent version. `static/client-component.json` records
its canonical source, shared inputs, build and asset hashes. Its paired-server
declaration identifies shared-input provenance, explicitly unqualified; actual
compatibility comes from Unified's exact-source test/release evidence. This record
does not hash itself; whole-static package checks include it. Released wheels and
source distributions contain complete assets and require no Node or client
checkout to run or reinstall. Raw source development requires Node.

## Local experiments

`python scripts/web_client_build.py dev` runs the prepared client's development
server. `python scripts/web_client_build.py build-development` installs deliberately
edited local output for an owned experiment or negative browser check, without a
qualifying component receipt. `.build/web-client` remains disposable: move desired
changes into the canonical client repository, then select that source. Preparation
refuses to overwrite unknown edits; save or discard experiments deliberately
before refreshing. A normal production build restores selected assets.

## Prepare a standalone copy

With Python, Git and Node available in the owned environment:

```sh
python scripts/web_client_source.py /tmp/my-web-client
cd /tmp/my-web-client
npm ci
npm run prepare:inputs
npm run build
npm run package:artifact -- /tmp/my-web-package PAIRED_SERVER_COMMIT \
  --source-record /tmp/my-web-client/.client-source.json
```

Choose a new task-owned directory. Preparation verifies selected source and
carries `.client-source.json`. In a Git-free source export, this standalone command
copies only the bounded production manifest; use normal root `prepare` or the
canonical client checkout for client tests. No Git metadata, dependencies or local
state is copied. `prepare:inputs` retrieves
only the exact public files pinned by the client; its README also documents
offline input preparation. A paired-server declaration is not a compatibility
claim. The selection record is not independent authentication if both source and
record are replaced.

## Refresh and retire the copy

From the clean selected client commit, run
`node scripts/client-source.mjs record /tmp/client-source.json`, then export its
tracked tree with `git archive HEAD`. Replace only `components/web-client/` and
update its source record together, retaining
`delivery: {"type":"directory","path":"components/web-client"}`. Review upstream
changes and qualify affected build and peer behavior. Shared catalogs, SDK and
normative contracts retain their existing owners. No wrapper package or lock is
generated in Unified.

When repository access is available, change delivery to `{"type":"repository"}`
and remove the copied tree in the same review. Preparation fetches the exact
commit through Unified's repository-access policy. Client verification, artifact
format and runtime do not change with delivery mode.

The copied tree is opt-in source-review context and excluded from Unified's
Python source distribution. Inspect selected source with
`python scripts/source_context.py --all --format paths --under components/web-client`.
Ordinary searches do not apply those attributes. Retire obsolete owned prepared
copies and dependencies after review, retaining useful finite evidence and
recoverable commits. Refreshing the copy changes the full-tree release identity;
historical evidence is never relabelled or exempted.
