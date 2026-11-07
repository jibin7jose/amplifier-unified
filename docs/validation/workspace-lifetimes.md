# Development workspace lifetimes

Development checkouts, transfers and test environments have an owner and a
purpose. Their lifetime ends when that purpose and remaining consumers end;
completion does not make every diagnostic artifact permanent. User work,
active reviews, dirty/unpushed source and necessary recovery evidence retain
their own disposition. Product history and Storage policies have separate owners.

## Read only the relevant record

`scripts/workspace_manifest.py` provides bounded discovery and updates for an
existing Linux development workspace's `WORKSPACE-MANIFEST.json`. Pass its exact path;
the tool does not infer another task's workspace from the current directory.

```sh
python3 scripts/workspace_manifest.py --manifest /workspace/WORKSPACE-MANIFEST.json list --filter review
python3 scripts/workspace_manifest.py --manifest /workspace/WORKSPACE-MANIFEST.json get myTask --field /development/dtu
```

List returns ten record summaries by default, with explicit pagination and
truncation indicators. Get reads the selected record or JSON Pointer. Neither
command reads the referenced files, probes an app, starts a worker or decides
that declared status proves a resource can be deleted. A small response does
not mean cheap storage: the legacy manifest must still be parsed in full.

## Give updates one entry point

Write the exact replacement record as JSON, then use `recordSha256` returned
by get. Unrelated record changes do not conflict. A conflict requires rereading the selected record and reconciling
the change; never blindly retry with a refreshed digest and stale content.

```sh
python3 scripts/workspace_manifest.py --manifest /workspace/WORKSPACE-MANIFEST.json put myTask --value-file /tmp/myTask.json --expect RECORD_SHA256_FROM_GET
```

The update uses the existing `.workspace-manifest.lock`, preserves other records
and file permissions, and atomically replaces the file after syncing its bytes.
It returns a compact receipt instead of echoing the record. It creates no
revision archive. The supplied record file is caller-owned temporary material;
remove it after a successful update when it has no other purpose.
Get also returns an explicit missing-record identity for a new key; absence is
different from a saved null value. A field read still returns the whole record's
identity, so replacing that record requires preserving its other fields.

All writers must use the same update API or cooperative lock. A final identity
and content check detects an observed uncoordinated write but cannot make a
writer ignoring the lock safe. This first step does not shrink or replace the
legacy schema, authorize resource deletion, or introduce a cleanup schedule.

## Move from a ledger to current ownership

The accepted direction is one compact current-resource index: owner, exact
identity, state, holders/dependencies, retention condition and recovery pointer.
An agent requests a resource; its creation wrapper records those facts. Bounded
views remain derived from that index, not a second editable authority.

Migrate writers first, reconcile legacy owners and holds, then cut over the
index. Unknown legacy records remain explicit unresolved entries. Do not infer
retirement from a date, path name, missing heartbeat or a claimed completed status.
Keep one finite migration snapshot rather than permanent copies of every revision.

Closeout follows task/resource events and standing policy: release holders,
verify exact ownership and current consumers, preserve recoverable source, and
retire only eligible named resources through Git/DTU/filesystem owners. Routine
eligible cleanup needs no additional user approval. Interrupted or unknown effects
remain recoverable; retry must not target a reused path, PID or container name.
No universal closeout hook or background reconciler is claimed by this tool.

The proposed defaults for the later closeout mechanism are immediate retirement
of consumed scratch/transfers once recovery is settled, seven days for successful
temporary diagnostics and thirty days for compact completion receipts. Named
active holds and unresolved recovery remain current state. Old requests for an
expired resource ID must never recreate it or fall back to a path. Larger durable
deliverables need explicit disposition, not these temporary-material defaults.

## Focused verification

`python3 -m unittest discover -s tests -p test_workspace_manifest.py` checks
bounded projections, exact field selection, conflicting writers, interruption,
unknown-field preservation and file identity. It requires no application runtime.
Resource deletion adapters and manifest cutover need their own failure/recovery
checks before adoption; these reader/writer checks do not establish those effects.
