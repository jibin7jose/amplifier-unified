# Live approvals, computation and usage admission acceptance

The configured `terra` provider (`gpt-5.6-terra`, `high`) was exercised through a fresh private Unified home, the real worker, the composed Work bundle and production Chromium 153.0.8010.12. Evidence is combined from explicitly attributed runs; **there was no single green run of the entire scenario**. The [redacted report](live-controls-2026-09-20.json) contains 30 verified checks and their sources.

Run **g** verified browser Deny/Allow for nested `tool_exec` Bash calls, their authoritative denied/completed receipts, the denied file's continued absence, an allowed file/output, and browser Stop terminating an actual managed command with exit `-15`. It then verified Python and Node variables across cells and a real agent reusing the browser-created Python variable to calculate 43 and write `calculation.html`. The run ended at a harness endpoint mistake after those checks: computation controls use `/api/actions`, while the session-command endpoint only permits conversation commands. That mistake is corrected.

Run **j** independently verified the real computation UI, approvals, both persistent runtimes, unsent draft and selected conversation preservation, reconnect and no browser errors. The harness serializes browser gestures with its approval-click loop; simultaneous Playwright pointer actions had caused intermittent missed cell clicks. This serialization never holds an execution/approval wait.

Run **k** reopened g's saved artifact with runtime execution disabled. It verified a **visible** canvas iframe showing 43, and unchanged canonical transcript hashes. This replaces g's too-early render assertion, which could find the iframe while the saved-artifact library still covered it. The screenshot was also visually inspected. Neither this reopen nor j submitted a model prompt.

Run **i** admitted one real model call, saved the provider usage receipt, set a revision-bound one-token limit and observed `generation.failed` / `provider.error` when attempting another turn. The runtime correctly ended `stopped`; the harness initially waited for `idle`. The corrected predicate accepts the observed terminal states. Run **l**, with execution disabled, inspected the same saved records: exactly one model call, the persisted limit and rejection, blocked admission and no fabricated model reply. It submitted no new provider request.

The provider adapter reported normalized input 3, output 7, total 10, cache-write 12,635 and cost USD 0.0316775. These are adapter counters, not raw wire totals; the harness did not collect the raw API payload. The installed provider source (`f0c94b001f70e11c0a668eb9886639b63cc0bbbf`, `amplifier_module_provider_openai/__init__.py:5233–5295`) subtracts cache-write from vendor input and computes normalized total as input plus output. The host preserves those fields. The cache-write count is excluded from that normalized total, so this run does not prove an inclusive token budget or account quota. The subsequent [Core contract audit](../token-accounting.md) corrected budget consumption without changing those normalized counters. A [passive check of the actual saved receipt](saved-budget-2026-09-20.json) passed all11 checks: inclusive input12,638 and total12,645, unchanged cost and all10 original evidence hashes, with zero new provider calls. The capacity browser and invocation-boundary regression tests separately verify enforcement against that inclusive total.

Two product fixes resulted from the live run:

- `2ad4083c`: admission waits for a stopped worker's confirmed exit before accepting new work. A public stopped event can precede OS process exit. Timeout leaves the admission fenced; no input is replayed. Real process tests cover a new PID, exactly one control, and explicit retry after uncertain shutdown.
- `ffb60ba2`: explicit **Create runtime** prepares a fresh session owner after Stop. Read, execute and reset of an old computation generation do not restart or replay it. Missing-owner errors use the ordinary application error boundary.

Approval cards additionally expose their existing opaque request ID as `data-approval-id`, allowing the browser test to target the exact approval while multiple requests are visible.

Validation: 23 focused stop/computation/profile/cleanup tests; 42 stop/retention/handoff tests; 33 runtime/profile tests in the earlier focused pass. Production frontend build passed. One kernel test-suite teardown hang did not recur on isolated and combined reruns; no production cleanup change was inferred from that transient test result.

## Reproduction

The harness requires the reviewed Unified environment and local sibling module sources shown in `profile()`. It preserves the selected provider's model and effort. The profile is a frontmatter-only `includes` overlay: flattening loses namespaced resources, and an overlay Markdown body replaces the original Work instruction.

```sh
PYTHONPATH=".:$MODULE_ROOT/repos/amplifier-module-hooks-approval" \
  /path/to/reviewed-runtime/python scripts/work_profile/live_controls_acceptance.py \
  --allow-live --provider terra --bundle /path/to/work/bundle.md \
  --module-root "$MODULE_ROOT" --output /tmp/new-private-acceptance-folder
```

`--ui-only` exercises Python/Node browser cells without model prompts; `--budget-only` uses one real provider baseline and then rejects future admission; `--compute-only` omits denial/Stop. No mode reuses a private home or automatically retries an uncertain side effect. `--allow-live` is required even for UI-only mode because it clicks real synthetic execution approvals.

The synthetic overlay alone enables unrestricted managed stdin and adds real `hooks-approval` with no auto-approval rules and explicit computation approval. Normal settings/policies are unchanged. Unexpected permission tool types are rejected by the harness. Selected credentials are read in memory and copied only into the mode-0700 fixture home. After shutdown, literal copies are redacted in generated text; database files are scanned without mutation. Cleanup reports counts only. Raw history and diagnostics remain outside the repository.

`saved_artifact_acceptance.py --run <finished-run> --output <new-folder>` and `saved_budget_acceptance.py` provide passive follow-up inspections with runtime execution disabled.

No spreadsheet calculation engine was available, so spreadsheet formula recalculation is untested. Image interpretation, physical audio, provider quota retrieval, worker-descendant accounting, deployment and release are outside this acceptance.

## Immediate composer direction (November 2026 correction)

The normal composer sends `conversation.send` immediately while a run is active.
It captures that run's generation and the next-message configuration revision.
The worker chooses the actual mounted transport; the browser does not infer native
support from a model name or silently enable `native_provider`. Explicit opt-in is
preserved. A documented model, opt-in, selected mount and effective SDK endpoint
qualify the transport, but do not establish that a response is currently steerable.
`native.status.activeNativeResponse` reports the actual response/generation when
ready. The final provider admission still rechecks ownership, selection and Stop.

Native receipts distinguish socket sent, provider accepted, provider pending and
distinct successor applied. “Sending live direction” is not provider acceptance.
“Accepted; applying live direction” is not application. Provider-pending direction
is explicitly queued **at the provider**, not held by the app for its next step.
Boundary-only models keep ordinary next-request context insertion. Tools already
running are not interrupted or undone. With no steerable native response (including
a running tool or a successor already pending), the message is retained with a
no-send explanation; it is not silently replayed in another generation.

The maintained offline browser check uses production built assets, RuntimeManager,
a subprocess Worker, Core and loop-live. Only the external provider transports are
scripted. It clicks the normal composer, holds the parent response in flight and
requires `response.steer` on the wire **before** releasing its terminal frame.
Separate checkpoints test sent/accepted/applied labels, one distinct successor,
one final, no artificial second turn, ordinary-model boundary behavior,
tool/no-response refusal, exact generation/config revision and private-draft reload
without replay. This is not a paid-provider or deployed-demo acceptance claim.

```sh
AMPLIFIER_TEST_PYTHON=/path/to/qualified-runtime/bin/python \
  AMPLIFIER_DIRECTION_EVIDENCE=/path/to/new-evidence \
  npm run test:immediate-direction-browser --prefix tests/web
```

### Same-instance human demo acceptance plan

The deployment owner stages the final reviewed commit **with its matching built
assets** on existing instance `53b4cd12`; this correction does not launch a DTU,
change its service or submit a paid request. Keep the selected provider, exact
model/effort, opt-in and progressive-output setting unchanged.

1. Verify the deployed commit, build ID and loaded worker/provider/loop provenance.
2. In the normal browser composer, send a task that keeps a model response active.
3. While that response is in flight, click Send with a short new direction.
4. Verify sending → provider accepted → distinct successor applied, not an app-held
   next-step label. Compare the exact input's sent receipt with the parent terminal.
5. Verify one final for the directed response and no new user turn or repeated tools.
6. Check a boundary-only model separately: its input waits for the next request,
   without claiming immediate native effect. A tool/no-response phase must explain
   the absence of immediate delivery; Stop and changed configuration must refuse
   stale input without replay. Preserve any newer private draft.

Keep socket-send, provider acceptance, successor application and model obedience
as separate observations. If no delayed dispatch is observed, report the wording
defect and the discriminating test rather than inventing a queueing defect.
