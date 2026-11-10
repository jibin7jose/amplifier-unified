# Saved-conversation loading

Run the production build, then
`AMPLIFIER_PERF_EVIDENCE=/tmp/history-loading.json node tests/web/history-loading-performance.mjs`
inside an owned DTU. The fixture creates disposable native JSONL histories,
uses the real history reader, HTTP/SSE delivery and production frontend, and
rejects any model start or send. It verifies that source files remain unchanged.

The three histories contain 1,000 messages, 10,000 messages, and 10,000 messages
plus 10,000 completed tool calls. Each message contains about 2 KB of Markdown.
Each scenario gets one cold application-cache open and two warm reopens, on
loopback and an emulated remote connection. Remote emulation uses Chromium CDP:
100 ms latency, 10 Mbps download and 2 Mbps upload. The OS file cache is warm
from fixture creation; this is not a cold disk or actual internet measurement.

Timing starts when the browser dispatches `session.select` and ends after the
latest saved message enters the rendered DOM and two animation frames pass.
It does not substitute the appearance of the shell or a loading indicator for
saved content. Server history-load duration is reported separately. Network
instrumentation includes actual SSE frames and decoded API bytes through the
selection's acknowledgment, rather than measuring only `/api/view` requests
(the selected conversation may arrive entirely over SSE).

## October 9, 2026 candidate measurements

Product source: `4acefec39e68862c0669b39909615936ac4392b9`, the locally combined
candidate for PRs #495, #499, #500 and #504. Owned ARM64 Linux DTU, Python 3.13,
Node 22 and single-process Chromium. Eighteen measurements, not a statistical
latency distribution:

| Link | Cold application cache | Warm reopen |
| --- | --- | --- |
| Local | 612–746 ms | 355–1,193 ms |
| Emulated remote | 621–739 ms | 396–603 ms |

Every initial render contained 100 messages. The largest decoded SSE frame was
241,406 bytes; the largest total observed decoded API delivery during selection
was 596,516 bytes. Largest source transcript: approximately 22.2 MB; associated
tool log: approximately 26.8 MB. Original source hashes were unchanged and no
model work started. Frame and DOM bounds are assertions; measured milliseconds
are reported rather than enforced as a machine-dependent CI threshold.

These results do not reproduce the reported intermittent ten-second Mac Mini
loading delay. They do not qualify first-send runtime preparation, cold OS
caches, huge attachment payloads, concurrent active conversations, initial
catalog discovery, full activity expansion, or an actual Mac Mini/remote route.
Those remain separate investigations; these measurements are not evidence
that the user's observed delay is fixed.

Full logs and measurements are retained in the owning workspace's
`output/history-loading-20261009`. The fixture stops its owned server and removes
its temporary source/state directory on normal shutdown and SIGTERM.

## Recheck after the reviewed changes merged

Product source: `b190c3b7777d3d910e381de739b9407550bfa42e`; harness integration:
`5eb56173`. The same eighteen scenarios passed against merged main in the owned
DTU. This is a separate run, not a replacement for the earlier measurements.

| Link | Cold application cache | Warm reopen |
| --- | --- | --- |
| Local | 708–868 ms | 396–705 ms |
| Emulated remote | 652–948 ms | 371–540 ms |

Every initial render again contained 100 messages. The largest decoded SSE frame
was 240,362 bytes; total decoded API delivery peaked at 597,105 bytes. Original
histories remained unchanged and no model work started. Evidence is retained in
`output/history-loading-integrated-20261009`. The limitations above still apply;
this run does not establish that the reported remote Mac Mini delay is fixed.

## Reopening previously expanded native history

The initial-load benchmark did not cover a native history window already
expanded by an earlier visit or another reader. Such a window could bypass
browser message paging. Browser projection now caps passive native messages at
100 independently of the retained server window. Message cursors and source
ordinals preserve access to retained pages and older disk history.

`node tests/web/retained-history-browser.mjs` runs the production frontend
against a real 10,000-message JSONL transcript with 1,000 rows already retained.
It checks the initial display bound, navigation to unloaded history, retained
and disk paging, duplicate prevention, an independent reader's history
expansion, and reading-position restoration after switching and reloading.
Source hashes must remain unchanged and no model work may run. Explicitly
loading earlier pages still adds rows during that visit; this is not complete
scroll virtualization.

A separate synthetic loopback comparison reopened the same fully retained
1,000-message transcript. The frozen candidate at `7e73300c` rendered 1,000 rows,
delivered 2,448,365 decoded API bytes, and painted saved content in 2,467 ms.
The repair based on main `57f4f472` rendered 100 rows, delivered 316,988 bytes,
and painted in 637 ms. These are individual samples with warm OS caches in an
owned ARM64 Linux DTU, not latency percentiles or proof about a particular
remote user's route. The largest SSE frame fell from 2,371,018 to 239,761 bytes.
Both runs preserved source history and performed no model work.

## Non-LLM tool and history bridge timing

Set `AMPLIFIER_RUNTIME_TIMING=1` at process startup to opt into correlated phase
recording. It is off when unset, `0`, or any value other than `1`. This controls
the added phase recorder, not all existing performance diagnostics. The host
and runtime workers must inherit the same setting.

The bounded diagnostic ring retains invocation/call/bridge identities and
integer monotonic phase marks, not arguments, results or conversation bodies.
Instrumentation adds one-way timing observations, not a request/response for
every phase. It must not change Work admission, origin evidence, Stop handling,
terminal ordering or durable saves. Missing correlation remains unknown.
Clock-domain hashes conceal raw boot/namespace identifiers but remain linkable
within that domain. Session/call identifiers, timestamps, errors and stderr can
also identify an environment; review generated evidence before sharing it.

Use recorded phase marks, not diagnostic arrival order, to compare:

1. Tool wrapper/origin/Work-check wait versus the original tool body.
2. Worker protocol write, host dequeue, handler, reply and worker resumption.
3. History refresh, lock wait, detached input copy and transcript-read work.

Cross-process differences require matching verified clock domains. Dequeue is
when the host reader consumes the frame, not kernel arrival. Thread CPU cannot
represent an asynchronous wait. Native checkpoint timing is a separate
continuation boundary; the early tool-post stamp does not include every later
hook, persistence operation, publication or browser paint.

### Provider-free real-pipe fixture

From the repository root, in an isolated task environment with the locked
development dependencies and installed loop-live, context-simple and skills
modules, run:

```sh
python -B tests/fixtures/non_llm_latency.py output/non-llm-latency --large --loaded
```

The output directory must not already exist. `--probes-off` disables phase
recording in both processes; omitting `--large` uses the small data shape and
omitting `--loaded` removes the background event sequence. The fixture selects
the timing flag explicitly in both arms.

Core dispatch, the skill tool, Work checks, AppService, SQLite, subscribers and
Worker/RuntimeManager pipes execute their production paths. The finite driver
and fixed local shell-command tool are synthetic. No provider is mounted.
The fixture exercises ordinary **unassociated** Work checks; commissioned
origin/Stop coverage comes from `tests/test_work_origin.py`, not this workload.
The skill loader receives the same explicit warm-up in every arm.

The large case retains 400 messages of roughly 12,000 characters, 13,500 tool
nodes with nested input payloads and 4,693 accounting receipts in one conversation, with a second
small conversation issuing four concurrent calls. Loaded cases offer eight
terminal events at 25-ms spacing. Reports retain exact call joins, tool
outputs, ordered subscriber revisions, preservation-check results for private
drafts, issued frames and durable state, source shape, failures and bounded
worker stderr.
Subscriber snapshots may skip intermediate revisions; final content and
nondecreasing delivery are the requirements.

Host and worker CPU cover different intervals; shell-child CPU is excluded.
RSS values are separate process-lifetime peaks, including setup, not simultaneous
peaks to add together. Background callback entry is not service-lock admission.
This fixture does not qualify native checkpoints, browser paint, paid-provider
transport, production latency or a universal instrumentation-overhead bound.

### Passive history snapshot invariant

History queries detach only their 17 top-level retrieval fields, rather than
copying unrelated execution and worker graphs. Keep all retained message rows
and nested fields: native/voice occurrence alignment and peer-attribution
ambiguity checks can depend on messages outside the requested page.

For a `ColdRecord`, first use the existing detached wrapper. Calling its getter
on the live record materializes fields, removes cold references/cached summaries
and invokes the touch callback; ordinary whole-record deep copy did not have
those side effects. Test marker/summary/touch preservation as well as returned
content. Preserve absent keys, cross-field copy aliases and current-scope
revalidation after the asynchronous read.

The focused tests compare complete list/search/read results with the old
whole-copy input for local/native, managed/web-owned and root/child history.
They also cover getter-backed fields, recency/identity ambiguity, voice identity,
returned forged attribution, off-page duplicate identities and mutations or
scope changes across the await. Avoided graph traversal is not bounded message
work: full retained messages and transcript parsing remain.

When adding a retrieval consumer field, review this snapshot boundary alongside
`_rows`, `_identity`, native/voice alignment and peer attribution; extend the
full-copy oracle case with a value that makes the new field observable. A new
unrelated presentation field must not expand the retrieval snapshot.

### October 28, 2026 isolated comparison

**Partial improvement, not a repair of the reported tens-of-seconds delays.**
The fixture demonstrates removal of one expensive history input copy. The
separate loaded control wait remains, and the production symptom was not
reproduced.

Both arms include published `0.33.0` (`100327052`) plus opt-in timing:
baseline `ec12544d501fcc3081a2b64858287e7e056dabba`; candidate source archive
SHA-256 `a5cc944b5c7a0c7ecc6b7374202279cfcae22803b73f9f59289b9ba7fc609683`.
The candidate differs only in `history_query.py` and its tests. The fixture
bytes and the 85-package Python 3.13.15 ARM64 Linux environment are identical.
Subsequent documentation edits do not change the measured runtime.

One frozen sequence ran loaded A/B, B/A, A/B, then small idle A/B, with probes
on in both arms. Eight batches completed once; no retry, resized workload or
discarded sample. Whole batches took 3.97–5.73 seconds; transfer, execution and
evidence collection completed in 229.38 seconds within the 900-second cap.
These are three loaded pairs and one idle pair, **not p95 estimates**.

| Pair | History wall A → B (ms) | History copy A → B (ms) | Command Δ (ms) | Skill Δ (ms) | Actions Δ (ms) |
| --- | --- | --- | --- | --- | --- |
| Loaded 1 | 1190.295 → 728.987 | 479.095 → 0.994 | +20.116 | +15.766 | +31.332 |
| Loaded 2 | 1230.781 → 765.109 | 475.695 → 1.003 | +17.886 | +27.506 | +18.652 |
| Loaded 3 | 1236.558 → 731.053 | 476.970 → 0.974 | −16.587 | −25.488 | −18.858 |
| Small idle | 95.223 → 111.827 | 0.318 → 0.106 | +19.190 | +19.173 | +10.396 |

Δ is candidate minus baseline. The loaded paired median history-wall change
is **−465.672 ms**, range −505.505 to −461.308 ms. The paired median copy change
is −475.996 ms. Separate arm medians (1230.781 and 731.053 ms) are not the
paired median change. The small idle history observation worsened by 16.604 ms;
one pair cannot establish its cause or demonstrate regression freedom.

Command, skill and action controls show mixed signs, not a demonstrated win.
Candidate loaded controls still took 651–694 ms, missing the proposed 500-ms
target. Their send-to-handler waits were about 642–683 ms while Work-check
handlers took at most 0.070 ms. History copying began after the three controls
had finished in every loaded batch. Removing that later copy cannot explain
away their earlier delay. Small idle controls remained below the proposed
100-ms target and candidate history reads below 1 second.

Loaded host CPU over request scheduling through drain fell by a paired median
452.626 ms. Small idle host CPU rose from 45.301 to 77.534 ms. Host process-lifetime
peak RSS ranged 200,420–203,704 KiB before and 154,984–158,616 KiB after; setup
is included. These observations are not whole-application CPU or heap bounds.
Timing-off overhead against the published baseline remains unmeasured.

All 32 calls, 96 timing rows and 48 background events were retained. All 16
subscriber sequences were nondecreasing; loaded final revision 18 followed
background revision 13, idle final revision was 10, and queues drained.
Preservation checks passed; consumer errors and retained worker stderr were
empty. The five returned message rows were checked exactly. This does not
constitute an independent byte comparison of every retained state object.

Functional qualification: four new traversal tests failed on the unchanged
baseline. Review found cold-source mutation in the first candidate despite
135 passing tests; a strengthened cold-preservation test reproduced that defect.
The corrected candidate passed **137 focused tests**, zero failures/errors/skips,
in 69.66 seconds. Earlier 85-test and 19-check preflight passes remain evidence
for their earlier exact inputs, not a substitute for this qualification.

The next unresolved performance question is shared-host work before handler
entry, not expensive Work-check computation. No further measurement is implied
by this record; both bounded experiment sequences are closed.
