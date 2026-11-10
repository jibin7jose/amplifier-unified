# Independent web client source

The [Amplifier web client](https://github.com/microsoft/amplifier-client-web) owns
its source and dependencies. While that repository awaits public availability,
[`web-client/`](web-client/) supplies the selected source here.
[`web-client.source.json`](web-client.source.json) selects its canonical commit,
build-input digest and delivery location. Maintain changes in the client repository
and refresh this whole snapshot; it is not another place to author the client.

## Normal build and tests

In the owned development environment:

```sh
npm ci --prefix frontend
npm run prepare:view --prefix frontend
npm test --prefix frontend
npm run build --prefix frontend
```

The build prepares and verifies `frontend/.client`, checks the current catalogs,
SDK and branding against its lock offline, runs the client's own build, then
installs its artifact in `amplifier_web/static`. The client has an independent
version. Released wheels and source distributions contain complete assets and
need no Node or component checkout to run or reinstall. Source development
requires Node. Current HTTP/SSE and the later ACP work remain separate boundaries.

`static/client-component.json` records the canonical client source, shared inputs,
build and asset hashes. Its paired-server declaration is the shared-input source
revision, explicitly unqualified; actual compatibility comes from Unified's
exact-source test/release evidence. This metadata does not hash itself; the
existing whole-static release/package checks include it.

Unified owns `frontend/tests` and `frontend/commands.json`. Its package and lock
entrypoints are generated from the client and those commands. `prepare:view`
creates ignored copies at `frontend/src`, `public` and the small build helper
paths so existing integration tests exercise selected source without a second
authored client. It refuses to overwrite experiments.

`npm run dev --prefix frontend` and `npm run build:development --prefix frontend`
use that editable view. The latter supports deliberate negative browser tests;
its changed output cannot supply the selected component's release receipt. A
normal build restores selected production assets. Move desired changes into the
client repository, then refresh its selection.

## Prepare a standalone copy

With Python, Git and Node available in the owned environment:

```sh
python scripts/web_client_source.py /tmp/my-web-client
cd /tmp/my-web-client
npm ci
npm run prepare:inputs
npm run test:portable
npm run build
npm run package:artifact -- /tmp/my-web-package PAIRED_SERVER_COMMIT \
  --source-record /tmp/my-web-client/.client-source.json
```

Choose a new task-owned directory. Preparation copies tracked source, verifies
its selection and carries `.client-source.json` into the output. Git-free source
exports supply only the client's bounded production manifest; use its repository
for standalone tests. No Git metadata, dependencies or local state is copied.
`prepare:inputs` retrieves the exact public files pinned by the client; its README
also documents offline input preparation. An exact paired server declaration is
not a compatibility claim. The source record selects trusted inputs; it is not
independent authentication when both source and record are replaced.

## Refresh and retire the copy

From the clean selected client commit, run
`node scripts/client-source.mjs record /tmp/client-source.json`, then export its
tracked tree with `git archive HEAD`. Replace only `components/web-client/` and
update its source record, retaining
`delivery: {"type":"directory","path":"components/web-client"}`. Regenerate
dependency entrypoints with `python scripts/web_client_development.py manifests`.
Save or discard local experiments deliberately before refreshing the test view.
Review the upstream changes and qualify affected build and peer behavior.
Shared catalogs, SDK and normative contracts retain their existing owners.

When repository access is available, change delivery to `{"type":"repository"}`
and remove the copied tree in the same review. Preparation fetches the exact
commit through Unified's repository-access policy. The client verifier, artifact
format and runtime need no delivery-specific changes.

The copied tree is opt-in source-review context and excluded from Unified's
Python source distribution. Wrapper and selection remain visible. Inspect the
copy with `python scripts/source_context.py --all --format paths --under components/web-client`.
Ordinary searches do not apply those attributes. Refreshing the copy changes the
full-tree release identity; historical evidence is never relabelled or exempted.
