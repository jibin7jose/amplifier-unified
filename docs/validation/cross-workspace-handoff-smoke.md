# Reviewed independent-chat smoke

This additive checklist covers the ordinary `New chat requests` review introduced
for #786. It does not change the frozen 199-workflow / 669-outcome UX baseline.
The contract is in [new-chat-handoff.md](../clients/new-chat-handoff.md), with
prior assessment in [new-chat-handoff-786.md](new-chat-handoff-786.md).

## Owner and execution

`cross-workspace-handoff` belongs to the nightly/manual extended browser-check lane.
It runs against the selected immutable published revision, not automatically per release. The registry
adds one scenario (79 extended scenarios, 122 unique source browser scripts)
without changing the original 65-scenario hash or adding a duplicate foreground
release gate. Focused affected-change execution uses the same invocation:

```sh
AMPLIFIER_TEST_ARTIFACTS=/absolute/owned/evidence node tests/web/cross-workspace-handoff-browser.mjs
```

The built client must match the candidate backend. The fixture starts the real
host on an ephemeral loopback port, supplies synthetic provider metadata and a
recipient runtime, and uses temporary state plus existing synthetic folders.
It creates two independent browser processes/clients. No real account, provider,
model, external workspace or native-device permission is required. Both browsers,
the listener and temporary state are retired when the scenario ends.

## Checklist and target behavior

| Stable outcome | Required observation | Desired UX |
| --- | --- | --- |
| H786-01 proposed review | Exact canonical target, title, full plain-text brief, bundle and model are visible; backend session/runtime counts do not change. | Understand the exact work and destination before approval. |
| H786-02 retained pending review | Reload restores the exact existing review reference, immutable identity/hash and source draft through normal client attachment. An unrelated client viewing the same source cannot see it. | Restore the reviewed request without granting access to future proposals or other clients’ reviews. |
| H786-03 cancellation | `Not now` records cancellation; no target, runtime or input is created. | Decline without side effects. |
| H786-04 one confirmation | While the real confirmation is held, duplicate clicks dispatch only one request. The fixture drops its HTTP response after host acceptance. | One clear pending operation; uncertainty requires inspection. |
| H786-05 reconcile accepted work | `Check status` and reload recover the same durable target/input IDs; backend counts and input contents remain unchanged. | Never replay work because its acknowledgement was lost. |
| H786-06 client isolation | Source selection/draft and a second browser's independent selection/draft survive creation and delivery. Only `Open chat` navigates the requester. | Keep current work in place until explicit navigation. |
| H786-07 stale folder | Removing only the empty reviewed fixture folder before confirmation shows failure, with no new target or input. | Changed target authority requires a new review. |
| H786-08 bounded UI | Desktop, 390px and 320px screenshots, no horizontal overflow or uncaught page error; no idle receipt polling. | Review remains readable on narrow screens and stays quiet when idle. |
| H786-09 explicit request starts directly | Agent dispatches `session.handoff.start` with no confirmation UI/action; one target and exact input survive a lost real reply, a fresh-command retry with the same request ID, reload and status reads. Both clients retain their drafts/selection; the other client cannot see the receipt. | Act on the user's request without asking again; keep suggestions reviewable. |

`result.json` records elapsed time, isolated client identities, the real accepted
receipt and backend witnesses before/after. These single-run timings are early
regression witnesses, not latency percentiles. A healthy local synthetic flow
should acknowledge a click promptly and complete within a few seconds; provider
startup/response latency is outside this fixture. Playwright timeout success is
not itself a performance qualification.

The recipient emits only `Synthetic recipient accepted the brief.` The test
proves delivery, **not** that the requested file was read or its work completed.
Host restart, permissions/configuration changes and interrupted delivery are
covered by focused backend cases; do not infer those guarantees from a browser
reload. Native-provider/device acceptance remains separate.

## Current evidence

The frozen release candidate based on `fd8484c49` passed on October 17, 2026 in
the owned `dtu-unified-codex-dev-20261014` instance. The backend and built client
both reported `0.20.173`; frontend build ID was `ce042f1a141277a9`. The exact
uncommitted candidate is bound by 83 matching host/DTU SHA-256 entries in
`output/handoff786-20261017/browser/final-qualified/source-preflight.json`;
the integration commit supplies its final Git identity.

All eight outcomes passed in **6.512 seconds** with Node 22.23.3 and Chromium
153.0.8010.12. Proposal-to-visible was **62ms**, pending-review reload **238ms**,
and released confirmation-to-reconciliation **317ms**. One confirmation created
one target and accepted one exact initial input; the retained before/after
witnesses show no repeat after acknowledgement loss or reload. The idle
observation was 1,700ms without another receipt read. Both private drafts,
explicit-only navigation, cancellation, stale-folder refusal and narrow layouts
passed with zero uncaught page errors.

`output/handoff786-20261017/browser/final-qualified/` contains `result.json` and
desktop/320px/390px screenshots. The initial failed reload and an earlier passing
candidate remain distinct in adjacent evidence directories. The failure exposed
missing review-reference retention during normal client attachment; this was
fixed before the final run. The registry ownership checks separately passed
18 cases. No live provider, native device or real user workspace was exercised.

On this ARM DTU Chromium required process-local `--no-zygote --single-process
--disable-gpu` launch arguments. The scenario uses separate browser processes to
keep both clients independent; it does not change browser launch policy elsewhere.

## Clarified direct-start behavior (0.20.174)

The user's October 16 clarification supersedes mandatory confirmation for an
explicit request. The existing eight review outcomes remain covered; H786-09
adds direct execution without changing the frozen inventory or scenario count.
On the owned DTU, the combined scenario passed in **8.057s** against backend and
built client **0.20.174 / b1233792339f6a5f**. Direct start reached acceptance in
**464ms**, with zero confirmation actions and exactly one additional chat/input.
Review visibility was 81ms, reload 237ms, and confirmation reconciliation 315ms.
The recipient remains synthetic; this proves dispatch and recovery, not live
provider completion. Evidence and exact source/build hashes are retained under
`output/direct-handoff786-20261017/browser/root-admission-qualified`.
Earlier candidates remain retained separately; the final run includes root
transport/generation admission checks and 83 matching host/DTU source/assets hashes.

The questions smoke also co-renders a real pending handoff and question, saves
drafts, reloads, cancels the handoff and checks exactly one question card before
continuing the original answer/retry/voice/mobile checks. Shipped 0.20.173 failed
that uniqueness check (three cards); distinct sibling component keys in 0.20.174
passed. This cross-component check prevents the previous isolated handoff smoke
from hiding an adjacent question-card regression.
