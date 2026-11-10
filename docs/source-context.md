# Finding source and its owners

Start with the changed behavior, its owner and the relevant contract. Use this
map to find those inputs without loading built bundles or old evidence into
every review. It describes current source entrypoints, not new authority or
proof of the target architecture.

## Select paths before reading content

Run from a checkout with Python's standard library and Git; no app environment
is needed. The command reads Git membership/attributes and file metadata only.

```sh
python scripts/source_context.py
python scripts/source_context.py --format paths --under amplifier_web/agent_state.py --under tests/test_agent_state.py
python scripts/source_context.py --format paths --kind contracts
python scripts/source_context.py --format json --under frontend/src
python scripts/source_context.py --format paths --kind evidence
python scripts/source_context.py --format json --all --under amplifier_web/static
```

The default summary counts every tracked path. Path/JSON output normally omits
`generated`, `evidence`, `assets` and `locks`; select a kind explicitly or use
`--all` to include them. It lists paths, never concatenates source. `--under`
matches exact files or directory prefixes, not globs. Select related tests and
consumers as well as the changed implementation: this is not dependency closure.
Use `--format paths --null` when passing filenames to another program.

Ordinary `rg` searches do not apply Git's context attributes. After choosing the
relevant roots, search their selected paths (this example uses GNU `xargs`):

```sh
python scripts/source_context.py --format paths --null --under frontend/src --under frontend/tests | xargs -0 -r rg -n --with-filename -- 'stateRevision'
```

`-r` prevents an empty selection from becoming an unrestricted search. Git also
supports attribute-aware searches without `xargs`, for example
`git grep -n 'stateRevision' -- ':(attr:source-context=source)frontend/src' ':(attr:source-context=tests)frontend/tests'`.
Both searches keep tests alongside source; add the actual consumers and contracts
for the changed behavior rather than treating directory selection as complete coverage.

[.gitattributes](../.gitattributes) owns classification and generated-diff hints.
The tool uses effective Git attributes, including local overrides, and index
membership with current working-tree sizes. Stage intended new files to include
them; untracked files, dependencies and private scratch work are not collected.
Unknown classifications stay visible by default. `--check` reports them with a
nonzero exit status before filtering; it is an optional maintenance check, not
a new release gate. Classify a new root deliberately instead of hiding it.

Generated output still ships and must pass existing build/package checks.
The temporary [independent client delivery](../components/README.md) is classified
as generated context because it is mechanically copied from its owning repo.
Its selection and maintenance guide stay visible; use `--all --under components/web-client`
to inspect the copy. It is the selected production build source, not another authored client owner.
Source bootstrap files in `components/web-client/public` and shipped documentation in
`docs/shell` remain authored inputs. Frozen UX inventory/control JSON is retained
evidence; its maintained delta and completion-policy ledgers remain test inputs.
Dated documents can contain current follow-ups, so documentation is not
automatically historical. Labels select review context, never deletion candidates.
The library-performance guide remains maintained documentation, including its
historical comparison. Exact collaboration qualification records and the older
reviewed-candidate manifest are opt-in evidence; the current canonical/accepted
catalog manifests remain test inputs. A generated-looking source file without a
reproducible provenance record, such as the device client's `observed-dom.js`,
stays visible for source review.

The individually listed live-control, schedule, budget and native-provider JSON
reports are retained evidence, including the failed continuity run. Their linked
guides remain visible for current direction, reproduction and evidence limits.

## Follow the canonical boundary

| Work | Current source entrypoints | Governing meaning |
| --- | --- | --- |
| Admission, action receipts and errors | [service](../amplifier_web/service.py), domain handlers and [save scope](../amplifier_web/action_save_scope.py) | [Current wire](clients/live-sessions.md), [client state](../contracts/client-state.v1.md) |
| Runtime and provider lifecycle | [runtime](../amplifier_web/runtime.py), [worker](../amplifier_web/runtime_worker.py), [host adapter](../amplifier_web/host) | [Agent boundary](../contracts/agent-protocol.v1.md), [request recovery](../contracts/model-request-recovery.v1.md); native Core/Foundation own native mechanisms |
| History, catalog and display | [shared state](../amplifier_web/shared_state.py), [history directory](../amplifier_web/history_directory.py), [cold display](../amplifier_web/cold_display.py) | [Working set](../contracts/working-set.v1.md), [state/storage design](architecture/state-and-storage.md); derived views are not new history authorities |
| Browser transport, commands and private view | [main](../components/web-client/src/main.jsx), [state stream](../components/web-client/src/state-stream.js), [draft journal](../components/web-client/src/draft-journal.js) | [Client state](../contracts/client-state.v1.md), [client experience](../contracts/client-experience.v1.md); stream, action and optimistic state have distinct causality |
| Agent instructions and readbacks | [app guidance](../amplifier_web/app_guidance.py), [agent state](../amplifier_web/agent_state.py), [receipts](../amplifier_web/agent_receipts.py) | [Agent results](AGENT-RESULTS.md), client privacy and receipt contracts; optional skill absence does not remove supported actions |
| Domain capabilities | Relevant `amplifier_web` handler, portable `amplifier_*` package, frontend consumer and domain tests | Domain guide under `docs/`; [component boundaries](../contracts/component-boundaries.v1.md) |
| Shell, canvas and appearance | [shell](shell), [SDK](../packages/shell-sdk), [frontend shell](../components/web-client/src/shell) | Public extension/appearance contracts; saved user skins and independent components remain consumers |
| Test and release mechanisms | [Python tests](../tests), [browser registry](../frontend/tests/browser-checks.json), [scripts](../scripts) | [Performance validation](validation/performance.md), [release qualification](validation/release-qualification.md); exact evidence and independent modes remain distinct |

The [family vision](architecture/VISION.md) and [client direction index](clients/direction.md)
separate target design, current contracts and demonstrated behavior. Follow those
owners rather than copying normative rules into another inventory. For an
individual change, inspect its real producer, consumers and focused tests; a
directory name is a starting point, not proof of isolation.

Review generated assets through [source/build identity and verified hashes](validation/release-qualification.md#recovering-a-successful-frontend-build-after-asset-parity-fails).
Retain frozen workload revisions and failure/recovery evidence when linking a
new result. Fewer lines or bytes alone do not establish a simpler design.
