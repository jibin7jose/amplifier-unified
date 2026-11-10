# New task per scheduled occurrence

Source base: Unified integration `89c301e68570c3eb619fbfb6f4aa27620aa4f2f4`.
This evidence covers the additive P05 new-task mode, not deployment or a remote
provider rerun. No account settings, production homes or production hosts changed.

## Implementation

`destination` is optional and defaults to `same_task`. Explicit `new_task` preview
binds title, finite turn cap, current workspace/bundle, provider/model/effort and
private configuration digest. Actual user provenance is required to activate.
Each due occurrence durably reserves a unique destination before shared creation,
then records the new task identity before the existing scheduled-input admission.
The original task is independent; selection, draft, history and objective remain
intact. Configuration settings are inherited through the existing private files;
history, task/goal, usage, receipts, workers and approvals are not copied.

Creation/submission uncertainty preserves the reserved identity and never retries.
Wrong-destination reports are rejected; incoming run relationships and source run
history are available through shared agent/UI actions. Task creation is visible
in the sidebar and the schedule's run card links to its destination.

## Checks

- 31 scheduling/queue/policy tests passed initially. After adding native identity
  collision coverage and tightening guards, 22 new-task/existing-schedule tests
  passed; the final 14 new-task tests passed with incoming-run discovery included.
- 47 shared service, agent chat controls and client-isolation tests passed.
- `node frontend/tests/scheduled-new-tasks-browser.mjs` passed against production
  host, worker, Foundation and loop-live with a deterministic **local** provider.
  It selects the new-task mode through the production UI, reviews exact model/high
  effort, runs two occurrences into distinct fresh user-owned tasks, observes
  actual assistant events and completion, verifies unchanged source draft and
  selection, then pauses/restarts the scheduler and verifies identical receipts,
  session count and generation events. The source has no saved objective.
- The existing schedules browser passed concrete same-task preview/activation,
  quiet monitor comparison, pause/reload/review, unknown/reconcile/cancel and mobile
  checks after the new UI controls were added.
- Production frontend build and whitespace checks passed.

The worker fixture used loop-live source `3ab5c02a1fd9a883c0ec8d7552e124f4e2e0ff05`
and installed context-simple. It creates its own private home, temporary local
provider/bundle and random listening port, then closes all workers and deletes
its fixture. The provider returns a fixed marker without network or credentials.
Paid calls: **0**. This proves the full new-mode host/worker/browser path and
reconnect behavior, not speech, physical microphone or remote-provider behavior.
The previous separately attributed Terra/high same-task due-run evidence is not
relabelled as a new-task provider run.

Run with the existing development environment (including current loop-live):

```sh
python -m pytest tests/test_schedule_new_tasks.py tests/test_schedules.py tests/test_schedule_policy.py tests/test_scheduled_input.py
python scripts/web_client_build.py build
node tests/web/scheduled-new-tasks-browser.mjs
```
