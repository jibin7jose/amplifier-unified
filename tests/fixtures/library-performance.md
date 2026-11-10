# Library transport and interaction regression

Run these commands in the owned DTU, with the repository's Python environment,
prepared client dependencies, host test dependencies, and Playwright Chromium
installed:

```sh
python scripts/web_client_build.py build
npm --prefix tests/web run test:library-performance
```

The build uses the selected client under `.build/web-client`; the host integration
harness lives in `tests/web`. The default uses packaged production assets served
by the real backend. This launches real local
HTTP services backed by disposable SQLite/presentation storage and synthetic
native-index input. It does not read personal chats, call providers, or start
model runtimes. Native ingestion, state projections, actions, SSE, persistence,
and agent access execute production code.

The default sweep is `summaries:workspaces:rootChats`:

- `50:10:50`
- `3000:50:3000`
- `5000:100:5000`
- `22500:4000:4000` (a large catalog containing root and worker histories)

Useful options follow `--` after the npm command:

- `--cases=50:10:50,5000:100:5000` limits the sweep.
- `--samples=3` changes repeated measurements.
- `--output=/tmp/library-performance.json` saves full metrics.
- `--label=before` labels a baseline.
- `--app-root=/path/to/immutable/source` tests old backend and frontend sources
  with the current harness. That source must contain its matching built assets.
- `--dev` uses the target's prepared `.build/web-client` source through Vite, for
  diagnosis. Prepare that selected client and its dependencies first. Compare
  only runs using the same production/development mode; historical `--app-root`
  production comparisons continue to use the target's matching built assets.
- `--assert-bounded` verifies that state and lightweight action responses and
  each measured SSE frame stay below 2 MB, with at most 250 transmitted session
  and workspace rows. `--max-state-bytes=...` overrides the byte ceiling.

Measurements include HTTP body size, time to response headers, complete read
and JSON decoding, lightweight view.update roundtrips, publication counts,
backend projection/save time, settings and submenu click-to-paint latency,
SSE bytes, long tasks, and DOM size. Timings are reported, not hard-coded as CI
pass/fail requirements: compare runs on the same machine with the same scenario
and sample count. Byte/row bounds are the stable regression checks. The suite
also verifies one publication per lightweight action, full catalog navigation
through agent actions/state, bounded sidebar rendering, and zero runtime calls.

Each settings cycle also visits Capabilities, triggering the real automatic
bundle-list read before switching to Maintenance. `maintenanceSettledMs`
includes its pending action/view requests and a 250 ms quiet window; it is
reported separately from click-to-paint so optimistic rendering cannot hide a
server backlog. The focused bundle-publication tests enforce the smaller
publication budget and atomic catalog/completion receipts, while workspace
reconciliation tests enforce linear lookup growth without wall-clock thresholds.

The index input is generated in memory; initial filesystem discovery is not part
of this benchmark. The largest case creates 4000 empty workspace folders to keep
availability checks representative. Source code and application data remain
isolated from the user's running instance.

## Recorded comparison

On the same macOS ARM64 host, three samples per case using packaged production
assets, v0.10.3 versus the bounded-library change (September 19, 2026):
This historical run preceded the additional Capabilities step described above.

| Session summaries | State bytes before → after | View action median | Settings paint median |
| ---: | ---: | ---: | ---: |
| 50 | 213,781 → 210,766 | 6.9 → 4.7 ms | 46 → 26 ms |
| 3,000 | 3,557,963 → 388,209 | 208 → 24 ms | 193 → 36 ms |
| 5,000 | 5,819,457 → 397,884 | 346 → 37 ms | 790 → 35 ms |
| 22,500 | 28,544,279 → 558,986 | 1,649 → 137 ms | 1,923 → 39 ms |

The largest case includes 4,000 root chats in 4,000 workspaces; remaining
summaries are worker histories. Total SSE traffic during three settings cycles
dropped from 314.6 MB to 6.73 MB. The fixture verifies zero model starts and
preserved off-page agent search/pinning. These results characterize the measured
metadata/transport workload, not every possible transcript, attachment, initial
disk discovery or network connection.
