# Independent web client source

The extracted [Amplifier web client](https://github.com/microsoft/amplifier-client-web)
is maintained separately. While that repository awaits public availability,
[`web-client/`](web-client/) supplies the same source here. The small
[`web-client.source.json`](web-client.source.json) selects its canonical commit,
authored build-input digest, and current delivery location. This is a temporary
delivery copy, not a second place to maintain the client.

The existing `frontend/` still builds the shipped application. This pilot has its
own version and pinned shared inputs; it is not automatically compatible with
the latest server. Its package command requires an exact paired server commit,
and that declaration alone is not qualification. Current HTTP/SSE behavior and
the later ACP/AHP work are separate boundaries.

## Prepare and build

From the Unified checkout, with Python, Git and Node available:

```sh
python scripts/web_client_source.py /tmp/my-web-client
cd /tmp/my-web-client
npm ci
npm run prepare:inputs
npm run test:portable
npm run build
```

Choose a new task-owned directory. Follow workspace isolation requirements for
application builds and tests. Preparation copies only tracked source, verifies
the selected client inputs and carries `.client-source.json` into the output.
It does not copy Git metadata, dependencies, build output or local state.
`prepare:inputs` retrieves the exact public files pinned by the client's lock;
see the [client README](web-client/README.md) for offline input preparation and
the supported fixture scope.

Package the built copy with its source record:

```sh
npm run package:artifact -- /tmp/my-web-package PAIRED_SERVER_COMMIT \
  --source-record /tmp/my-web-client/.client-source.json
```

The artifact keeps the canonical client commit, rather than reporting the
containing Unified commit as client authorship. The record is a trusted input
selection, not independent authentication if both source and record are replaced.
Edited copies can be used for development; package them only after selecting the
corresponding committed client source and refreshing its record.

## Refresh and retire the copy

Maintain changes in the client repository. From its clean selected commit, run
`node scripts/client-source.mjs record /tmp/client-source.json`, then export its
tracked tree with `git archive HEAD`. Replace only `components/web-client/` with
that export and update `web-client.source.json` from the new record, retaining
`delivery: {"type": "directory", "path": "components/web-client"}`. Review the
upstream change and qualify the affected build/peer behavior before adoption.
No shared input catalog or normative contract gets a new owner in the copy.

Once repository access is available, change `delivery` to `{"type":"repository"}`
and remove `web-client/` in the same review. The preparation command then fetches
the exact canonical commit using Unified's existing repository-access policy.
The client verifier, artifact format and runtime need no delivery-specific change.
Publishing the client repository and changing Unified's shipped client are
separate decisions.

The copied tree is opt-in source-review context and excluded from Unified's
Python source distribution. Its wrapper and selection stay visible. Inspect it
with `python scripts/source_context.py --all --format paths --under components/web-client`.
Ordinary searches do not apply those attributes. Adding or refreshing this copy
still changes the full-tree release identity; existing release evidence is not
relabelled or exempted.
