# Optional native provider responsiveness

Unified retains request-boundary steering by default. A bundle can explicitly
set `session.orchestrator.config.native_provider: true` to use the optional
**provider-owned** `amplifier_module_provider_openai.native` adapter. This host
contains no Responses wire-protocol implementation. The installed provider must
include that module and its `native` optional dependencies; the existing runtime
already includes WebSockets. During local review use a local provider source
override; after the provider change is merged use its existing `@main` source.
No models, defaults, credentials, or source pins are changed by this feature.

Only the currently selected, exact `OpenAIProvider` instance at the official
`https://api.openai.com/v1` endpoint is wrapped, before normal
observation/capacity/surface wrappers. The installed provider's
`_capabilities.get_request_policy(final_model).supports_steering` owns model
eligibility. As documented on November 6, 2026, its exact eligible IDs are:

| Model | Documented eligibility | Offline host/worker steering | New live qualification |
| --- | --- | --- | --- |
| `gpt-6-astra` | Yes | Covered | Not performed for this adoption |
| `gpt-6-sol` | Yes | Covered | Not performed |
| `gpt-6-luna` | Yes | Covered | Not performed |
| `gpt-6.1-sol` | Yes | Covered | Not performed |
| `gpt-6.1-terra` | Yes | Covered | Not performed |

This is not a family-prefix match or an account entitlement check. GPT-5.6,
unknown suffixes, invented snapshots and malformed IDs are not admitted.
The [current OpenAI steering guide](https://developers.openai.com/api/docs/guides/steering.md),
refetched November 6, specifies GPT-6-family steering and excludes GPT-5.6 and
earlier; the provider registry supplies the exact documented IDs above.
Azure/subclass drivers, proxies, credentials/ports/query/fragment additions to
the endpoint are not qualified by OpenAI's policy. A missing module,
unsupported model/endpoint, preexisting instance wrapper, or disabled option
reports ordinary boundary steering. A later selection change immediately uses ordinary transport and requires
remount before that new identity can use native transport. Changing effort does
not reuse the old native identity or poison it as an uncertain send. Finite children retain their
ordinary execution path.

Endpoint admission inspects the effective SDK route without constructing a
client: an existing/injected client's `base_url` wins; otherwise an explicit
provider `base_url` wins over `OPENAI_BASE_URL`, then the official SDK default
applies. Only strings, `httpx.URL`, and the installed SDK's own URL type
(`httpx2.URL` on this locked graph) are normalized. Unknown objects are not
qualified via `str()`, and a client with no known URL does not inherit a default.
The effective URL must retain the strict HTTPS authority/path checks: no
userinfo, explicit port, query, fragment or path parameters. URL objects use
their existing SDK-normalized representation; the host does not guess a prior
spelling. Unsupported routes before native installation retain the ordinary
provider and can complete normally through the selected wrapper.

The same effective-route check runs at final native send admission. A client URL
change or replacement during awaited preparation prevents that create/steer
send. Config/environment changes cannot override an already initialized
client's route. Active or unknown native state never falls back to ordinary
requests. After restart on an unsupported route, retained unknown sends keep a
recovery-only provider fence: native support remains false, no SDK client is
created by status, and no request is replayed.

Status uses the provider's pure assembler on an empty request for the current
selection, then its final JSON-overlay expansion. It does not count tokens,
create an SDK client, scan conversation history or dispatch inference. The
provider still assembles and qualifies each actual final request. A native
request must agree with the host's effective model and exact mounted instance,
including selected-wrapper model and configuration overrides. Neither the
selected preference nor a provider default can overrule a final `extra_body`
model. Pending/unknown native state refuses ordinary fallback after a change.

The pinned loop intentionally disables its boundary-only anchored input
capability for native mounts. Unified's worker control uses a host-owned native
bridge for those explicit mounts, binding the input to its exact generation
before any await and checking it again at outbound admission. It never puts that
input into a later-generation inbox. Duplicate input IDs return their existing
disposition; reused IDs with changed input are refused. Unsupported attachments
are explicitly held rather than silently entering another turn. Owner, Stop,
capacity, approval and Work admission remain in their existing owners.

The Chat → Advanced → Context & recovery panel and `runtime.control` share `native.status` and
`native.compact`. Status is descriptive. Explicit compaction requires the session
and dependent work to be idle, a prior successful native request, and a settled
transport. It makes one provider request through the same capacity admission and durable
call receipt path as ordinary completions. Reported token counters are retained;
missing usage stays unknown. Missing accounting prevents compaction. An active
agent cannot compact its own
in-flight generation; it must defer until idle. Agent native controls are bound
to the calling session, including through the generic runtime-control alias.
Client-supplied actor labels do not grant authority.

Acceptance means a correction is queued. Only an observed successor response
marks it applied. Identity-only receipts are stored privately before sends and
on actual provider events. Restart reconciles submitting/running/pending state to
**unknown**, never success/cancelled. Unknown transport is not automatically
resumed or replayed; use explicit session recovery, or explicitly change transport
configuration. A locally cancelled await or closed connection cannot establish
remote cancellation. The provider rejects unsupported automatic-compaction /
steering combinations; there is no silent summary fallback.

`steering.pending` is retained as pending, with only an identity-bound
`awaitingRequiredInput` flag. The actual approved tool result (including an
actual denial result) returns through the normal tool/hook path on that same
connection. The accepted steer is not repeated. A disconnect or restart while
accepted/pending remains unknown; even an ordinary/utility request cannot replay
it. Applied evidence remains applied on restart. Provider progress remains off
on the pinned Streaming graph: that loop synthesizes final content-block events
and does not consume `native_progressive_output` to suppress duplicates. The
full-loop offline test verifies one public final message and one canonical
assistant message. Enabling progressive output requires an independently
qualified loop consumer, not merely setting the provider flag.

## Isolated deployment and demo seam

This source adoption starts at Unified `v0.58.0`,
`24c482667bdba57ff8a8faa076e4410b87b76866`. The original steering contribution
`f44710f67ae41e41a7bc82ba428c45e5dd2f242b` used merged OpenAI PR #143,
`264603a2fa1c69422aeaa6a8255c81ac928749e8`. The deployment graph now includes
accepted PR #144, `84d4c2d2f8a950af045501d0254932bd39fc687b` (tree
`5d169444a4c1f53a09e0e0d2bb22c2c2ad112506`), its direct successor.
That upstream fix removes SDK-local `parsed_arguments` and output-text `parsed`
annotations during capture and replay, including legacy saved dictionaries. It
retains API fields, explicit nulls, nested tool arguments and opaque values,
without rewriting transcripts. Reasoning-selector imports remain sourced from
the canonical provider policy. Unified does not reimplement this normalization.
The local offline graph uses Core
2.0.2, Foundation `8d26eceb9b7cafe6e93346f8f4294596bd45dcd1`, loop-live
`670f405f711658c3c38e81dbd50a35d3d8c2ac3c`, Streaming
`072d50c5bcc0f7c57ca4d336ef03915563fa7861`, context-simple
`550af35c5b8e7648c73a4c5be7c850affecc96ee`, OpenAI SDK 3.36.1 and WebSockets
16.1.1. Python is 3.14.7. Existing authored sources/pins, extras and model defaults
are unchanged by the follow-up; its lock change advances only the existing
OpenAI `@main` resolution from PR #143 to PR #144.

The fresh isolated environment's installed provider Python files were byte-checked
against the accepted checkout. SHA-256: `__init__.py`
`0aa2b782a95b497a5ff8d65e3619cd40b7ee12d29c13875efb13b63c1dbe3487`;
`_response_handling.py`
`fca36801fb534a7788085eaa7add2ab1a0b094aae4f59deb6c495d3e6e529c58`.
The sibling evidence file `current-provider-graph.json` records all provider
Python hashes, installed origins and affected dependency revisions.

Unified `v0.58.1` was assessed separately: its changes bind saved-configuration
readback to the captured worker and permit read-only diagnostics while recovery
is held. They do not change this provider's capture/replay or native admission
boundary. This fresh-DTU contribution therefore retains its explicit `0.58.0`
root base; it neither downgrades the production host nor imports unrelated
configuration recovery changes.

The deployment owner must update **both** the Unified source/wheel and the
session's selected provider source to these reviewed revisions in the single
already-owned isolated DTU. Updating only the provider leaves the old host's
Astra gate; updating only the host can leave an old provider without the policy.
Do not patch an installed production host or its caches. A source-run DTU can
use `uv sync --frozen --group dev` from this commit; an existing worker's bundle
must still resolve the selected OpenAI module to the same merged provider, not
an older module cache. The source override seam is that bundle's provider
`source`, not global credentials or settings. Keep all other source pins intact.
The exact provider source is
`git+https://github.com/microsoft/amplifier-module-provider-openai@84d4c2d2f8a950af045501d0254932bd39fc687b`.

The follow-up checkout is `native-steering-source/amplifier-unified-native-steering-current`
on `feat/native-steering-current-provider`. Its transfer bundle is
`native-steering-source/native-steering-current-provider-endpoint-p2.bundle`, requiring
`f44710f67ae41e41a7bc82ba428c45e5dd2f242b` already present. Import that base
from the original steering bundle first when needed; the follow-up bundle does
not replace or mutate the independently reviewed original checkout. Deploy the
combined source only after review. No frontend inputs changed in this follow-up,
so the original verified static assets are retained byte-for-byte, not rebuilt.
The endpoint P2 correction follows `c90f1189f54e83a5870d5917dc61f7ba1d917ef2`;
it keeps the same accepted PR #144 provider resolution and dependency graph.
Synthetic native clients in the host/job/Work/UI fixtures now declare their
actual official endpoint rather than relying on a missing-client-URL default.
This exposes the provider's legitimate explicit tool-output cache-breakpoint
encoding; the pending-result fixture verifies that exact encoding and the
unchanged approved/denied tool-result text.

In a DTU-only demo bundle, retain `loop-live` and explicitly set
`session.orchestrator.config.native_provider: true`. Do not change default
models, or enable generic async/hosted multiagent features. Start a fresh chat
with one exact eligible model and its already-authorized official OpenAI
connection. Open Chat → Advanced → Context & recovery → Check provider support.
The panel reports eligibility, not live account qualification.

Send a sufficiently long, tool-free planning request. While its run is active,
use the normal composer Send action to submit a short correction. Inspect the
same input ID: queued at acceptance, applied only at successor creation, then a
single final answer. Use `runtime.control` operation `native.status` for the
identity-only send/phase receipts. A real demo must independently observe
create → created → one steer → accepted → successor created → terminal on one
connection, and report account/request failures honestly. Do not call it live
verified based on these offline tests. Launch/capacity and authorized paid
acceptance belong to the deployment owner; no guard should be weakened.

Focused local checks (scripted external transports, no live model requests):

```sh
uv sync --frozen --group dev
.venv/bin/python -m pytest -q tests/test_native_provider.py tests/test_native_job_recovery.py \
  tests/test_collaboration_input.py tests/test_conversation_steering.py
node --test .build/web-client/tests/native-provider.test.mjs
```

For the PR #144 changed-input check, the native host/job and SelectedProvider
suites run against the new frozen environment. The maintained SelectedProvider
regression additionally uses actual SDK parsed responses, Unified/Foundation
storage, a fresh Core/Streaming session and real SDK HTTP serialization to prove
identical clean count/generation input for both new captures and legacy saved
history, in streaming and non-streaming modes. Network transports are denied.
The existing qualification-clone canonical persistence cases and upstream PR
#144 parsed-output/selector cases are run read-only against the same installed
graph. Raw imports run under `python -I`; no source-path provider substitution
or live requests are needed. These checks qualify the changed graph boundary,
not account access or a new LAN demo. Unchanged browser, broad recovery and
frontend-build evidence remain the original contribution's evidence.

PR #144 changed-graph results: 156 native host/job cases and 44 SelectedProvider cases
passed; six canonical loop/disk/fresh-Core qualification cases and eight selected
upstream parsed-output/selector cases passed. No selected check skipped. The
upstream selection deliberately excludes unrelated cases, not failed checks.
Commands from the follow-up checkout (sibling repositories are read-only inputs):

```sh
.venv/bin/python -m pytest -q tests/test_native_provider.py tests/test_native_job_recovery.py tests/test_selected_provider.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/tests:$PWD" .venv/bin/python -m pytest --import-mode=importlib -p no:cacheprovider -q ../../amplifier-unified-qualification/tests/test_provider_modernization_boundary.py -k canonical_output_survives_real_loop_disk_and_fresh_core
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest --import-mode=importlib -p no:cacheprovider -o asyncio_mode=auto -q ../provider-openai-pr144/tests/test_canonical_response.py ../provider-openai-pr144/tests/test_consumer_compatibility.py -k 'parsed or sdk_field_removal or existing_agent_reasoning_selector_imports'
```

Endpoint P2 results: the affected host/job/SelectedProvider suites pass 250
cases (50 new endpoint checks). The exact native Work-effect/admission
regressions pass 22 cases. Tests use real SDK clients and URL types, environment
and config precedence, ordinary selected-wrapper HTTP serialization over
MockTransport, final create/steer admission barriers and fresh-Core unknown-state
recovery. Actual network transports are denied. A synthetic UI host
status/compaction fixture passes with its explicit endpoint; unchanged frontend
source/assets and the original browser evidence are reused, not rebuilt.
The final combined selection also includes the 27 request-recovery cases:
299 passed, no skips, with one pre-existing `service.py` SyntaxWarning.

```sh
.venv/bin/python -m pytest -q tests/test_native_provider.py tests/test_native_job_recovery.py tests/test_selected_provider.py
.venv/bin/python -m pytest -q tests/test_work_native_effects.py::test_native_early_tool_reaches_real_host_precondition tests/test_work_origin.py::test_installed_native_callbacks_bind_early_tool_to_exact_outbound
.venv/bin/python -m pytest -q tests/test_native_provider.py tests/test_native_job_recovery.py tests/test_selected_provider.py tests/test_request_recovery.py tests/test_work_native_effects.py::test_native_early_tool_reaches_real_host_precondition tests/test_work_origin.py::test_installed_native_callbacks_bind_early_tool_to_exact_outbound
```

The retained initial endpoint run exposed SDK `httpx2.URL` false negatives; the
retained restart negative control then exposed the missing recovery fence.
Both corrections pass the final selected cases. An overly broad regression
selection also included ten unrelated programmatic-tool cases without their
optional `amplifier_module_tool_exec` dependency. Those missing-package failures
remain in `endpoint-p2-native-admission-regressions.log`, not relabeled as passes;
the exact affected native nodes above pass without adding unrelated dependencies.

Private files under the owned session control directory:

- `native-provider-state.json`: bounded 128 identity-only protocol receipts and
  current outcome, with host session/generation/request binding.
- `native-provider-checkpoint.json`: the provider's opaque full JSON compaction
  window, capped at 16 MiB and bound to exact source prefix/config/model/instance.

Original native transcript storage is checkpointed before deriving a window.
Opaque values never appear in app state, tool results, the panel, or native
status. The host does not interpret/reconstruct hidden reasoning and does not
restore this format through context-managed summaries. An incompatible prefix,
request fitter result, or configuration discards only the derived state. Normal
history remains intact; compaction does not select a chat or alter drafts.

Validation includes provider protocol fixtures and a production-build app/Chromium fixture using shared controls and a synthetic SDK response. Desktop/mobile, agent status, reload, private payload exclusion, selection and draft retention are covered. Separate bounded live Astra checks observed accepted/applied steering and the completed successor. An additional continuation restored the original opaque checkpoint in a fresh provider and consumed it with only the new user input; no historical tool result was resent, no tool executed and no new compaction or retry occurred. Saved model defaults remained unchanged.

The first retained-checkpoint acceptance failed because turn-scoped serialization removed old reasoning when a new user arrived, changing the wire prefix. That failure is retained. The provider fix verifies the unchanged canonical/config binding and exact former wire prefix, permits only removal of old reasoning, and slices the actual current prefix. Changed text, tool results, policy or request fitting still reject checkpoint reuse. The corrected single live continuation returned the expected result with217 total reported tokens; this bounded result does not establish recovery of an interrupted remote generation.

A failed native computer result still fails closed in the current turn. Only a later explicit user message permits the provider's bounded textual history projection of that failed pair, without changing canonical history, replaying the action or clearing a safety halt. Remote cancellation confirmation remains a separate limitation.

Bounded acceptance reports: [steering](validation/native-provider-live-smoke.json), [retained initial failure](validation/native-continuity-live-report.json), [offline exact-state verification](validation/native-continuity-offline-report.json), and [corrected live continuation](validation/native-continuity-resume-report.json). Opaque checkpoint contents and credentials are excluded.
