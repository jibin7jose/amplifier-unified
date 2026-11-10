# Automation overview and safe recurring-schedule recovery

The [ratified contract](../contracts/automation-overview.md) adds a bounded everyday
entry point for #277 and a human review path for the confirmed #679 mechanisms.
It does not reproduce the historical M365 incident or diagnose that worker's exit.

## Checklist and ownership

| ID | Interaction and expected outcome | Current witness / target |
| --- | --- | --- |
| AUTO-01 | Navigation → Automation; first 30 schedule rows plus total and snapshot time, no history or runtime starts | 32 current + 1 cancelled synthetic schedules; first rows 85 ms, target under 1s warm |
| AUTO-02 | More/Previous, current/all filter and explicit Refresh; no steady polling or extra work | Browser paging and 1.2s idle request observation; target zero idle overview requests |
| AUTO-03 | Create in current chat / Open owning chat → existing Work & automation controls; draft and other clients retained | Renderer route, joined browser two-client draft/selection retention and held-response/closed-panel witness; no automatic navigation during review |
| AUTO-04 | Failed owner → Review future runs → explicit future resume | Exact prompt, workspace, model, bundle, cadence, notifications, missed-run policy and max turns; confirmation 121 ms, no owner preparation or old-turn replay |
| AUTO-05 | Stopped owner → review requires explicit acknowledgement; later Stop wins admission | Browser checkbox and focused final-admission race; target zero submissions after later Stop |
| AUTO-06 | Equivalent checkpoint serialization keeps review valid; real model/budget/module/permission/source choices invalidate | Focused canonical/raw hash comparison and real checkpoint followed by actual due tick |
| AUTO-07 | Two scheduler ticks submit once to a fresh target; failed/stopped owner stays unchanged | Joined real host/ledger + synthetic runtime; target exactly one input identity, no owner warming |
| AUTO-08 | Unknown prior work, stale schedule/configuration/Stop/execution, assignment suspension or missing workspace refuse | Focused host tests; no blind resume retry after lost browser acknowledgement |
| AUTO-09 | 390px review remains readable, controls usable, no horizontal overflow; confirm at mobile width | Packaged-browser screenshot and geometry; target all review/confirmation content reachable |

`tests/web/automation-hub-browser.mjs` is registered once in
`tests/web/browser-checks.json`, owned by extended Browser checks after
publication (and manually for affected changes before release). It starts and
retires its own loopback fixture with synthetic state and a fixed scheduler clock;
no provider/account credentials, active user schedules or external work are used.
`tests/test_automation_overview.py` belongs to the ordinary full Python suite;
`components/web-client/tests/automation-hub.test.mjs` belongs to client tests. Existing recurrence,
new-task lifecycle, unknown no-replay and peer-repair tests retain their owners.

## Invocation

In an owned DTU with the project's normal test dependencies and built client:

```sh
python -m pytest -q tests/test_automation_overview.py tests/test_schedule_new_tasks.py tests/test_schedule_handoff.py tests/test_schedule_projection.py tests/test_browser_check_runner.py
node --test .build/web-client/tests/automation-hub.test.mjs
AMPLIFIER_TEST_ARTIFACTS=/owned/evidence/automation node tests/web/automation-hub-browser.mjs
```

ARM DTU Chromium uses the repository's documented process-local single-process
launch adapter; this does not qualify native desktop or normal multiprocess ARM
browser startup. Browser timings are individual regression witnesses, not p95s.

## Recorded result

Qualified in the owned spark-2 DTU on October 17, 2026: **96 Python tests passed**
(including existing schedule lifecycle/repair/projection and coverage-owner checks),
**6 client tests passed**, and the production browser flow passed in **5.701s**.
Built client version 0.20.175, identity `f303c4316b2a6f8d`; Chromium 153.0.8010.12.
The source uses synthetic 33-schedule state (32 current, 1 cancelled); opening rows
measured 85 ms and confirmation to scheduled state 121 ms. The original owner stayed
stopped, one fresh target received one input across two due ticks, and a second
client retained its draft/selection. A held navigation response did not reopen
controls after the person closed Automation. Screenshots cover 390px review/control
access and the desktop overview. These single samples meet the stated warm 1s
interaction target but are not cold-start or tail-latency guarantees.

All raw browser attempts and exact source/build receipts are retained outside the
repository under `output/afk-approved-20261017/automation`. Initial setup failures
were a collapsed fixture navigation and Playwright's implicit single-page context;
the final harness opens navigation explicitly and uses an explicit context with
two independent clients. Focused test development also corrected synthetic
provider initialization and expected canonical Stop outcome (`skipped`, not failure).
No final assertion was removed to obtain a pass. Historical #679 exact differing
fingerprint/account state and original worker exit remain unproven; qualified
repairs cover equivalent saved controls, stopped/exited-owner review and actual
future admission without replay.
# Integrated review follow-up

The overview must show **Needs review** even when an older uncertain run is
followed by newer completed runs. Its bounded page now uses an indexed existence
check instead of inferring uncertainty from only the latest result. The existing
105-run recovery regression also checks this visible status. All 19 overview
tests pass in the owned DTU after this correction; no historical runs are loaded
or replayed.
