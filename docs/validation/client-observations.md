# Shared client observations: review acceptance

The second review reproduced continuation resets after unrelated browser drafts
and transcript deltas. Observation revisions now follow public facts separately
for sessions, notices and activity. Leased lightweight snapshots preserve coherent
continuation even while the live catalog changes. Browser links were refreshed
against main and production assets rebuilt from the merged frontend source.
See [the public contract](../clients/observations.md).

## Third review: completed traversal lifecycle

The review of `285258f15` reproduced 503 after eight fully completed changing
catalog traversals. Per-traversal leases now finish on the terminal response;
obsolete snapshots with no unfinished readers are reclaimed. Concurrent readers
retain independent leases, and per-collection quotas prevent a busy catalog from
starving counts or notices. Terminal retries retain only one bounded response for
30 seconds (up to 64 per collection), never a whole catalog.

Regressions cover 40 completed HTTP catalog traversals, 24 changing-generation
10,000-session count scans, concurrently unfinished readers across 25 completed
generations, terminal retries/expiry/eviction, first-page retry reuse and separate
collection capacity. Existing draft/transcript, sustained-change and fingerprint
coverage remains in place. The source is refreshed onto main `297bd0c82`; #583
has merged and this PR now targets main directly.

The hosted `metadata-malformed-retry` failure was a response-lifecycle race:
a waiter could catch an old document's request just before reloading away from
its body. The test now navigates to a blank document before installing the
interceptor/waiter and navigating back to the real application. Authentication,
malformed-response/retry assertions and the full media gate remain unchanged.

## Earlier owned environment and checks

Validation on main `af1d453c8` (Unified 0.20.123), restacked on browser-link
commit `188035616`: 308 host tests passed in the focused combined suite; the final
13 observation regressions also passed, including one added sustained-change
count test (309 distinct host tests total). Chat-link and provider-record-export
parser tests, both combined-stack browser acceptance checks, the independent
browser-link build and its browser acceptance all passed.

Checks run on spark-2 in the owned DTU `unified-macos-companion`. Observation
source/dependencies are isolated at `/opt/tempo-main-integration`; independent
browser-link acceptance uses `/opt/tempo-chat-links`. Hosts bind ephemeral DTU
loopback ports and use synthetic state, credentials and runtime only. The
existing port 8994 native preview and main/shared installations are unchanged.
Ownership, revisions, evidence and exact cleanup are in the workspace manifest.

- The host regression suite covers generic API and legacy adapter traversal beyond
  50 sessions and 200 notices, including 205 approvals in one chat; bounded and
  sparse pages; query/tamper/expiry rejection; incremental large counts; ETags;
  authentication/origin/client requirements; no history hydration; correct
  idle/loading/preparation/root/delegate semantics; shared read/fingerprint races;
  acknowledgement without approval; and explicit reply/stop/approval targets
  after selection changes, same-ID retry and changed-payload rejection.
- Shared web attention/navigation and existing live-client regressions exercise
  the refactored canonical helpers. Browser acceptance independently covers links
  through login, unavailable/deleted targets, reload and multiple windows.
- A real web client creates a manual unread marker, reads the generic summary and
  catalogs, acknowledges the exact fingerprint and verifies selection is retained.
- New authenticated-host regression traverses 10,000 sessions and 20,000 notices
  while interleaving real `view.update` saves and transcript-only invalidations.
  It verifies unchanged ETags/revisions, exact totals, no duplicate/missing IDs,
  and no public-index reconciliation for transcript deltas. Additional tests
  cover independent lifecycle/read/pin dependencies, deletion during traversal,
  continuous real changes during count continuation, lease expiry and admission
  backpressure without evicting existing readers.
- The synthetic construction benchmark traverses the same 10,000-session /
  20,000-notice catalog: 8 count batches, 50 session pages and 100 notice pages.
  With allocation tracing enabled, cold public-index construction was 0.505 s,
  maximum warm request 0.017 s, broad reconciliation 0.493 s, one scoped change
  including cold canonical SessionIndex setup 0.096 s, and 1,000 transcript-only
  invalidations together 0.0044 s. Additional traced peak memory was 22,797,243
  bytes. These are synthetic projection measurements excluding the preexisting
  catalog/network, not native idle usage or an end-to-end latency guarantee.
- **Tradeoff:** retained public facts and snapshot references use more memory
  than the prior live-state scanner (~22.8 MB versus ~0.81 MB in these runs).
  Cold construction and broad reconciliation remain O(catalog + notice slots),
  outside the bounded page/count loops. Warm continuation avoids reconstruction;
  transcript-only saves do not scan the catalog. Per-collection limits bound unfinished traversal leases and snapshot generations;
  completed traversals now release their leases immediately.

## Reproduce inside an owned DTU

```sh
uv sync --locked --group dev
.venv/bin/pytest -q tests/test_client_observations.py tests/test_companion.py \
  tests/test_live_clients.py tests/test_completion_attention.py \
  tests/test_disconnected_attention.py tests/test_active_client_projections.py \
  tests/test_chat_navigation.py tests/test_navigation_summary.py \
  tests/test_pinned_chats.py tests/test_questions.py tests/test_observations.py \
  tests/test_server_auth.py tests/test_storage_workspace_scope.py
.venv/bin/python tests/fixtures/client_observation_benchmark.py
python scripts/web_client_build.py prepare
npm ci --prefix .build/web-client
npm ci --prefix tests/web
python scripts/web_client_build.py build
node --test .build/web-client/tests/chat-deep-link.test.mjs
DTU_CHROMIUM_SINGLE_PROCESS=1 node tests/web/companion-browser.mjs
DTU_CHROMIUM_SINGLE_PROCESS=1 node tests/web/observations-browser.mjs
```

Node 22 and Playwright Chromium are required. Separate browser instances are used
for the authenticated and sign-in cases because Chromium single-process mode
cannot safely host multiple independent contexts. Test fixture startup errors and
synthetic-catalog teardown errors encountered while extending coverage were fixed
before recording final acceptance; they were not product failures.

## Explicitly separate work

The browser-link PR can land independently. The observation PR uses current
owner-authorized access; it introduces no scoped credentials or public native
setup/distribution flow. General device enrollment and capability enforcement
are a separate delivery slice with its acceptance criteria in the client contract.
No AHP/ACP adoption, account migration, production deployment, native-device
revalidation, or merge is included in this work.
