# Optional local status observations

Issue #290 adds a read-only, versioned local observation surface to the current
Core/Foundation application. It implements the vision's bounded working-set and
canonical-owner principles; it does not replace the host protocol, control work,
or establish another source of session truth.

## Enablement and package boundary

Unified publishes only when `AMPLIFIER_STATUS_DIR` names a private local directory.
Set it in the owned application's deployment environment. Unset means no emitter,
no writer thread, and no discovery files. Ordinary saves enqueue bounded identities;
projection is coalesced after 100 ms, and one background thread owns file writes.
No Settings change, default behavior composition, service restart, or live-host
migration is performed by this feature.

Unified includes the stdlib implementation at `amplifier_web.status_protocol` and
the `amplifier-unified-status` reference consumer. The same source builds a separate,
dependency-free `amplifier-status-emitter` wheel with public import `amplifier_status`
and the `amplifier-status` consumer. Build that wheel with:

```sh
uv build amplifier_web/status_protocol --wheel
```

Install the wheel in the CLI's environment, then compose this hook into the chosen
bundle. Supply an **explicit local source**: the CLI's normal resolver rejects an
installed module lacking a source hint. Obtain the installed directory using that
environment's Python:

```sh
python -c 'import amplifier_status, pathlib; print(pathlib.Path(amplifier_status.__file__).parent)'
```

```yaml
hooks:
  - module: hooks-status-emitter
    source: /absolute/installed/site-packages/amplifier_status
    config:
      directory: /absolute/private/status-registry
      bundle: optional-bundle-name
      include_plan_text: false
```

The CLI hook starts publication on an actual lifecycle event, not module validation.
It defers to the host when `application_host` identifies Unified and deduplicates
mounts through a coordinator capability. Optional attention events are subscribed
only when advertised through `observability.events`. No second plan or execution
authority is mounted. The two wheels own distinct imports and executable names,
so installing or uninstalling one does not remove the other's files.

Both consumers use `AMPLIFIER_STATUS_DIR`, otherwise `~/.amplifier/emitter`:

```sh
amplifier-status --directory /absolute/private/status-registry
amplifier-unified-status --directory /absolute/private/status-registry --watch
amplifier-status --directory /absolute/private/status-registry --instance INSTANCE_ID
```

No registry upload or network endpoint is provided. The consumer is a reference
JSON reader/watch loop for scripts and external UI consumers, not a new dashboard.

## Version 1 contract

Each atomic JSON snapshot has `schemaVersion: 1`, producer instance/host/version/
PID/hostname, session and optional parent identity, sequence, observation timestamp,
freshness TTL, and allow-listed `where`, `now`, `plan`, `usage`, `health` fields.
Paths and identities are intentionally available for local correlation; files are
private to the operating-system user. Prompt, transcript, draft, tool arguments,
results, approval/question text, objective text, and raw exceptions are excluded.

- `now.status` is a hint over observed lifecycle and attention. A pending question
  does **not** imply all tools/execution are paused. Explicit tool identities and
  start times remain available; missing data is null/unknown, not an invented fact.
- Plans come from successfully accepted `todo` tool receipts. Failed or invalid
  updates become unavailable. At most 200 statuses/counts/current-step indexes are
  retained. Unified's worker sends only a sanitized status receipt across its
  process boundary. Unified **never exposes plan labels**. CLI labels require
  explicit `include_plan_text: true`; default is redacted in both adapters.
- Usage, when available, states its scope; cumulative tokens never claim to be
  current context occupancy. Unsupported usage/provider/model fields remain null.
- `lifecycle` distinguishes live observation, orderly producer/session end, and
  `unobserved` capacity eviction. An ended observation is not a claim that work
  succeeded. `freshness: stale` after a crash is not a synthetic completion.
- No file is a command, lease, authorization grant, replay instruction, or evidence
  that a tool can safely run again. Consumers must never infer those semantics.

## Bounds and freshness

A producer keeps at most 256 live observations and 256 retained terminal/evicted
snapshots. Unified visits current runtime owners, the selected session and already
tracked hot observations, never the historical library or transcript. Settled work
gets a 30-second observation grace before ending. Under capacity pressure, settled
observations retire first; active observations rotate with an explicit `unobserved`
record. Repeated full publications rotate a stable oversized active map instead
of indefinitely revisiting its first entries. A rapidly mutating map may restart
its bounded iterator; coverage remains incomplete while over capacity.

Coverage includes omitted submissions, oversized batches, candidate upper bounds
and a conservative omitted lower bound. These are diagnostics, not total-work
counts. `complete: false` must remain visible to consumers. Selected-only or
explicitly scoped publication does not claim all host history was inspected.

Heartbeat defaults to two seconds, stale TTL to ten; each record is at most 64 KiB.
Snapshot writes use a private temporary file and atomic replacement (0600 files,
0700 directories on POSIX). A failed final snapshot retries on the existing
heartbeat, releasing its observation slot after a successful write. Write errors
cannot fail a model turn. A blocked
filesystem can make snapshots stale; shutdown waits at most two seconds for its
daemon writer. This is local-filesystem support, not a guarantee for arbitrary
network mounts.

Discovery reads at most 512 files/producer entries by default (configurable up to 4096),
rejects foreign/nonprivate/nonregular/symlink entries, and opens nonblocking to
avoid a FIFO trap. Overflow/invalid entries report incomplete results. Old producer
directories are not silently deleted: use exact `--instance` lookup to bypass
accumulated instances. Watch polling is bounded to intervals of 0.5–60 seconds.
No completeness claim may ignore directory or producer coverage warnings.

## Qualification boundaries

Focused tests cover disabled behavior, redaction, accepted receipts crossing the
worker event normalization boundary, optional event identity counts, atomic files,
crash expiry, bounded discovery/FIFO rejection, overflow rotation, session resume,
and cold-history non-hydration. `tests/fixtures/status_emitter_probe.py` runs an
installed CLI and a real Unified worker against a synthetic provider/local tools,
then reads both through the independently installed stdlib consumer. This proves
real lifecycle composition, not a live provider or all ecosystem adapters.

No separate Agent/ACP, TUI, recipe-specific progress, deep worker aggregation,
existing-plan history reconstruction, or remote registry adapter is claimed.
Those can consume/produce this schema after their own qualification. CLI and
Unified do not auto-enable publication everywhere merely because the package ships.
