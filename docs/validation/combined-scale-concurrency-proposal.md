# Combined copied-history and synthetic-runtime acceptance

Status: implemented after parent review, with a qualified first combined run.
The proposed design below is retained as design history; the measured
implementation and its deviations are recorded in the final section. The
existing read-only-execution replica and persistent development instance remain
available and unchanged.

## Purpose and boundaries

Measure ordinary navigation, selected recent history, and visible streaming with
the captured catalog/history scale and 0, 6, and 12 real RuntimeManager workers
in the same app. Workers use the already qualified deterministic local provider;
this does not qualify real provider latency, credentials, external integrations,
or historical tool execution. Keep the capture revision and event-tier completeness
in every result. Missing event shards cannot be reported as complete Work history.

## Proposed owned resources and mounts

Create a sibling `unified-scale-runtime-20261015.service` with its own private
RootDirectory, network namespace, loopback listener, Unix socket, browser context,
auth token, caches, and evidence. Use a separate account from the existing replica
where possible. No host port or existing preview service is replaced.

| Source | Namespace destination | Access |
| --- | --- | --- |
| Exact qualified app source and owned dependencies | Dedicated source/dependency paths | Read-only |
| Existing extracted native project directories | Original canonical native project paths | OS read-only binds |
| Existing extracted app content-addressed blobs | Blob lower layer | Read-only |
| New small owned blob upper/work directories | App blob merged view | Copy-on-write; never changes the lower |
| SQLite online backup of the sanitized app/catalog databases | Original app database paths | New writable branch |
| Fresh synthetic native project and workspace | Separate new project/workspace paths | Writable |
| Fresh config, runtime/session locks, cache, temporary files | Dedicated private paths | Writable |

Do not duplicate the expanded event corpus. Reuse the one extracted corpus through
read-only binds; retain its compressed immutable capture. Copy only the database
branch and small configuration required by the experiment. Inventory every bind
and verify its effective mount flags from inside the service namespace.

The writable app database can record navigation, indexes, caches, and synthetic
owners. This is not a claim that every imported metadata row is immutable. Copied
native files are OS read-only; copied app blobs have an immutable lower and a
disposable upper. Application-level owner guards additionally refuse imported
owner mutation. An overlay alone is not an execution or mutation fence.

## Execution admission contract

Start with an empty immutable allowlist. Before accepting browser requests, a
controlled fixture setup creates only new synthetic sessions in the fresh
synthetic workspace and records exact session IDs, native identities, workspace,
bundle/provider identity, and source-generation binding. Verify disjointness from
every captured identity. Freeze the allowlist for the entire measured process.
Failure to create or validate all bindings aborts startup.

Use the real RuntimeManager and ordinary worker bootstrap, with a narrow wrapper
that refuses start, send, warm, control, takeover, and child creation for every
owner outside that exact allowlist. An imported selected chat, a renamed owner,
or a caller-supplied synthetic-looking ID never grants authority. Revalidate the
full binding at each entry, not just ID membership. Disallow delegation entirely
unless a later reviewed protocol explicitly admits a new synthetic child.

Guard ordinary dispatch and agent app_bridge dispatch independently. Expand nested
shell commands before checking their actual effect. Permit bounded navigation and
read APIs for imported owners. Deny imported owner edit, delete, fork, resume,
stop, rename, rebind, configuration changes, publishing, schedules, and any other
mutation; do not depend on a small blacklist of known dangerous action names.
Unknown effect routes fail closed. Runtime teardown may stop only fixture-owned
processes and does not modify imported saved authority or handoff receipts.

Before app startup disable copied schedules, collaboration, observations that can
launch work, notifications, updates, publishing, connectors, and Smart Tools.
Use fresh private settings containing only the deterministic provider. Never load
copied account credentials, browser state, notification destinations, or forwarding
configuration. No historical task is replayed.

## Network and process proof before workloads

Require a private loopback-only namespace, empty proxy environment, no inherited
host sockets, and explicit fresh authentication. Prove external connectivity is
denied and only the owned app/local provider can be reached. A shared DTU loopback
is insufficient because other services may be listening there.

Before measuring, negative controls attempt imported-owner admission through user
dispatch, nested shell dispatch, agent bridge, direct runtime start/send/control,
rebound synthetic identity, and a new unlisted child. Assert rejection, no worker,
no provider call, and unchanged copied-file hashes. Positive controls require one
allowlisted worker to stream and stop normally. Record actual child cleanup.

## Measurement and reporting

Reuse the qualified real-worker benchmark recipe rather than introduce a second
runtime simulator: `tests/fixtures/runtime_concurrency_server.py`, its local
`amplifier_module_provider_concurrency` provider, and
`tests/web/runtime-concurrency-browser.mjs` in the integrated readiness
source. The fixture uses a real RuntimeManager with the qualified worker Python
and `runtime_worker.py`; the provider emits actual coordinator streaming hooks.
Its existing use of an already qualified interpreter bypasses fresh runtime
qualification and must remain an explicit limitation. Empty `bundle.app` and the
excluded image-generation module prevent implicit external modules.

Freeze one app/source/build/database baseline and capture tier.
Run 0/6/12 workers with exact admission/stream receipts and sampled copied-history
navigation in the same process. Record main-loop responsiveness, request latency,
stream cadence, CPU/RSS, query work, and cleanup. Pause capture I/O and other test
loads during comparable timing windows. Warm and cold observations remain separate.

Retain compact sanitized receipts outside the private data tree. No transcript,
credential, workspace content, or private title is committed. Do not describe an
inert scale-only run or a small synthetic-only run as combined acceptance.

## Cleanup

Record all resources under `readinessReplica20261015` before startup. After review,
stop only the sibling service, confirm no processes or open file references, unmount
its binds/overlay, and remove its writable database branch, blob upper, socket,
browser profile, and temporary source. Preserve immutable captures, the original
reading replica, persistent development chats, and compact evidence.

## Measured implementation, October 15

The sibling fixture is now implemented and its first combined run passed. The
sanitized numeric receipt is [combined-scale-concurrency-20261015.json](combined-scale-concurrency-20261015.json).
It binds application `09b9cbc596f280a67e4ebc6a1275fe2b5eb653d8` and built client
`0.20.165 / 135d5933af904245`. The copied catalog contains 27,508 native owners;
14 newly created synthetic owners bring the observed count to 27,522. All
captured owners retain their IDs and remain nonexecutable.

`scale_runtime_prepare.py` creates the sibling root/network namespace and an
online SQLite backup branch. Three app blob trees use a read-only captured lower
and private writable upper. Every existing copied native project is mounted
read-only. `scale_runtime_derived.py` permits only 112 exact app-owned `unified`
presentation-cache directories through separate COW overlays. Missing legacy
cache directories are represented in private project scaffolds; the resulting
project view is still mounted read-only except those exact cache leaves. No
canonical transcript or event file is writable. The native projects scaffold is
owned by the service account, as Foundation requires, with mode 0555; copied
project mounts retain their independent read-only enforcement.

Before startup, copied schedules and outbound automation are disabled. Fresh
configuration contains only the deterministic local provider; no copied provider
keys, authentication files, client views, or private drafts are used. The actual
network namespace contains only loopback and an external connection is refused.
The service uses its own port and token, never another DTU service's listener.
The original reading replica and persistent development preview remain intact.

`scale_runtime_fence.py` freezes at most 14 exact synthetic IDs plus their
workspace, bundle, native project, native identity, runtime identity, and
web-owned history bindings. It rechecks them at action, bridge, and real runtime
entry points. Shell command envelopes receive the same inner-action check.
Copied-owner sends, source rebinding, unlisted creation, child delegation,
scheduled input, takeover, arbitrary controls, and other unneeded effect routes
are refused. Restart reuses only the source-bound private synthetic-owner
receipt, validating all bindings again; it does not create another 14 owners.

Eleven pure boundary cases, seven real dispatch/bridge/runtime refusals, three
HTTP effect refusals, actual read-only file-open refusals, and a real single
synthetic worker stream/Stop cycle passed before measurement. Two initial setup
failures were retained privately: a root-owned ancestor refused by Foundation,
and a missing fresh `view.mode` default after excluding private browser state.
Both stopped before worker admission; their fixture corrections did not alter
product behavior or copied source data.

The browser then exercised actual public synthetic sends and Stop, and clicked
between a copied pinned chat and the synthetic foreground chat while 1, 6, and
12 real workers streamed. The stage called "0" has zero background workers and
one foreground worker. Capture was cooperatively paused only at a completed
shard boundary, and all other owned browser/test jobs were idle during the run.
Every worker exited afterward; capture resumed immediately.

Visible stream delay p95 was 129/169/229 ms, based on only 23 markers in each
six-second window. Copied-chat selection maximums were 346/268/311 ms over three
visits per stage. Cold-process send acknowledgements were 1.46/1.76/2.09 seconds;
first visible response was 1.78/2.27/2.55 seconds. Stop returned the UI in
895/872/865 ms. These satisfy the existing cold-send/first-visible three-second
and selection/stream/Stop targets in this short run. They do not establish the
governing 300 ms retained-worker send target, sustained stability, or external-provider
behavior. Whole-service memory peak was about 1.52 GiB and includes startup and
all stages; no per-stage resource attribution is claimed.

Only 10 of 32 event shards were verified at measurement time. Primary histories,
metadata, catalog and app blobs were complete; full Work/event-history fidelity
remains unqualified. The copied navigation samples were bounded recent pages of
one pinned chat and are warm revisits. The next useful scale discriminator is a
large workspace's cold open/filter/explicit pagination under 12 workers, with
request acknowledgement, query settlement and visible paint separated. The
previous approximately 1.44-second workspace observation is not resolved by
these chat-navigation samples. No broad rerun is needed to answer that question.

The containing DTU is limited to four CPUs (effective cpuset `0,9,16-17`) and
8 GiB memory, verified from Incus configuration and its cgroup. The sibling
service requests a six-CPU quota and 12 GiB memory limit, but those cannot expand
the parent allowance: effective ceilings are four CPUs and 8 GiB shared by the
DTU. Peer test/browser jobs were idle during measurement; retained idle services
share that parent. The parent CPU cgroup has no additional time quota.
