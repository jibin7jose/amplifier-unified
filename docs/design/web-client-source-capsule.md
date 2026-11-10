# Independent web client and shared build inputs

The web client's authored source, version and static-artifact packaging belong to
[microsoft/amplifier-client-web](https://github.com/microsoft/amplifier-client-web).
Unified selects and assembles that source through its
[component delivery entrypoint](../../components/README.md). While repository
publication awaits review, the selected in-repository copy supplies the same
source. It is generated delivery, not another place to maintain the client.
[CB](../../contracts/component-boundaries.v1.md) and
[CD](../../contracts/client-development.v1.md) govern this ownership boundary.

## Current shared-input export

Unified continues to own the three service catalogs, shell SDK and official
branding. The client consumes a finite, locked export from those owners:

| Inputs | Count |
| --- | ---: |
| `amplifier_web/{ai_services,provider_sources,voice_options}.json` | 3 |
| `packages/shell-sdk/{index.js,package.json,LICENSE}` | 3 |
| Six selected official branding files and `assets/UPSTREAM-LICENSE` | 7 |
| Root `LICENSE` | 1 |

The export contains **14 files**. The client reads its own version from its own
package manifest, so Unified's `pyproject.toml` is no longer an input. Generated
copies retain their canonical paths; no catalog, SDK or license gets a new owner.

From a checkout whose selected shared inputs are committed:

```sh
node scripts/web-client-inputs.mjs inputs /tmp/web-build-inputs
node scripts/web-client-inputs.mjs verify-inputs /tmp/web-build-inputs SOURCE_COMMIT
```

Use a new task-owned output directory. The shared-input helper supports only `inputs` and `verify-inputs`. Source-capsule `export` and
`package` commands are retired. Use the component entrypoint and client-owned
packager for current source preparation and artifacts.

`build-inputs.lock.json` records the canonical owner repository/commit and each
relative path, byte count and SHA-256. Export rejects changes to selected inputs;
unrelated working changes do not block it. It exports no backend implementation,
client source, dependencies, Git history or local state.

Verification is offline and checks the selected revision, exact file membership
and byte hashes. Missing, changed, extra or linked files fail. The selected lock
must come from a trusted canonical export, or its archive/digest must be pinned
by the component selection. Matching a self-contained lock does not authenticate
provenance if both the lock and its files have been replaced.

The client can receive the same bounded payload online or offline and keeps its
materialized inputs under an ignored build directory. Retain the small lock and
useful qualification receipt; remove superseded materialized inputs when their
build task finishes.

## Distribution and test ownership

| Source or check | Owner |
| --- | --- |
| Authored application source, public pages, client version and packaging | Web client repository |
| Temporary source delivery and tested default assembly | Unified distribution |
| Real AppService fixtures, installed composition and server-dependent browser checks | Unified integration owner |
| Shared catalogs, branding and shell SDK | Their existing Unified source paths |
| Family/client contracts | Existing contract owners, referenced rather than copied |

Unified installs assembled assets into `amplifier_web/static`, preserving serving,
installed frontend identity and shell validation. Published wheels and source
archives include those built assets. Installation and rebuilding a released
source archive need no Node or frontend download; raw development builds require
Node and locked dependencies. Independent client and server versions remain
separate identities.

A source selection or declared paired-server revision is not qualification.
Actual composition checks identify the selected client, server source and
installed static bytes. They retain authentication, saved-history, send,
reconnect, draft preservation and no-replay assertions. AppService fixtures keep
their Python helpers bound to the selected server source. A synthetic runtime or
provider does not qualify native execution, accounts, physical devices, AHP or
other version pairings.

## Earlier boundary evidence

The original source-capsule pilot proved a disposable independent HTTP/SSE build
before the ownership transfer. Its first history import preserved `LICENSE` and
`frontend/` from Unified `4854dbd9ec555690269747b7dbe273f008e9e179` (0.70.0).
The pilot package and immutable component records used their original source,
build-input and exact paired-server identities. Its standalone fixture and real
server composition checks established different, explicitly bounded claims.

The earlier shared-input export contained **15 files**, including
`pyproject.toml` while that build still read Unified's version. Those locks,
archives and qualification results retain their original identities and counts;
the current 14-file export does not relabel them. The verifier can still inspect
a retained lock against its recorded owner revision and exact bytes.

The [frozen pilot guide](https://github.com/microsoft/amplifier-unified/blob/816c7a67dbafc20bfb28723dd317c71d3bd2bb28/docs/design/web-client-source-capsule.md)
retains its original reproduction commands and evidence limits. Those commands
apply to that historical source, not the current distribution entrypoint.
