# Worker failure feedback qualification

The feedback projection owns the deliberately small, public-safe reproduction
summary. The runtime reader and session-health projection own the original
failure classification. Feedback must retain the built-in `worker_communication`
and `worker_exit` categories and `WorkerCommunicationError` and
`WorkerExitedError` types without exporting raw error details, custom exception
names, paths, conversation content or correlation identifiers.

This is a reporting correction related to [#842](https://github.com/microsoft/amplifier-unified/issues/842),
not a claim that its historical disconnections have been fixed. It follows
the family vision's truthful recovery principle and the shared clients'
[evidence and feedback principles](../clients/VISION.md).

## Maintained checks

Terminal support diagnostics use the existing worker and session-health owners.
The exact failed generation, input IDs, runtime owner and Stop revision bind a
supplement; a replacement generation or cancellation cannot acquire its cause.
A genuine `OSError` may contribute bounded numeric `errno` and `winerror`, never
its message, filename or local variables. These are diagnostic facts, not retry
authority or proof that a particular earlier operation failed.

If loop teardown raises while the exact `ManagerTurnError` remains in its actual
exception chain, preserve the primary manager failure and a separate bounded
`secondaryFailure` at stage `loop_exit`. Do not overwrite, redate or replay the
original failure, infer a cause from text, or classify the later history conflict
as proof of an external writer. The loop clears its current generation after
publishing the terminal event; the worker retains that exact observation only
until a new generation or terminal outcome replaces it.

- `tests/test_manager_exception_boundary.py` drives an actual Core/loop session
  through a synthetic post-provider `OSError`, then a real native-history guard
  conflict during teardown. The existing runtime reader, service, inspection
  export and application reopen retain both causes and the OS error number.
  Cancellation, Stop, replacement runtime/generation/input, foreign wrapper and
  private-message controls retain their original behavior. This does not prove
  a historical live disk failure or authorize recovery of an unknown write.

- `tests/test_feedback_diagnostics.py` delivers typed failures through the real
  runtime-event owner, then checks the on-demand browser/agent diagnostic action,
  frozen submission, mocked issue body, and saved receipt after application
  reopen. Reads do not publish or load history. Changed later failures do not
  rewrite accepted reports. Existing custom/malformed-value, size, opt-out and
  content-exclusion checks remain required.
- The existing registered `frontend/tests/feedback-browser.mjs` journey now
  presents a synthetic worker communication failure, opens reproduction details
  through the UI, and asserts the precise safe classification and absence of raw
  detail. It retains its on-demand collection, explicit refresh, opt-out,
  submission/reopen/reload, attachment and narrow-layout assertions. GitHub and
  providers remain mocked.
- `tests/test_runtime.py` retains real subprocess exit-code/type and communication
  cleanup coverage; `tests/test_session_health.py` retains the local category and
  recovery guidance checks. Reporting tests do not establish long-running worker
  reliability or a cause for the reporter's failure.

## Evidence still needed for the original disconnect

When a reader failure coincides with confirmed exit of its exact process, the
existing `runtime.ended` event settles that process's routing and execution
ownership. A reader error alone never establishes exit. Reader-side settlement
requires successful error publication and an already
known process exit; a disk-full publication failure can still leave stale routing
for the explicit, lease-protected recovery below. For an older failed root
whose process row is already absent, a new human message without an explicit
generation target may settle stale generation routing only after acquiring the
existing native writer lease. The service holds that lease through the command's
durable publication. Busy/preparing workers, unresolved placement or remote
ownership refuse recovery. Earlier errors, job effects and UNKNOWN deliveries
remain unchanged; only the new message is sent. Explicit generation-bound
corrections never become new work. `tests/test_failed_generation_send.py` covers
both the real process-exit boundary and the row-absent service recovery, including
lease exclusion, refusal and duplicate-command cases.

The public report shows an open browser event stream and a stopped chat runtime.
Those are capture-time facts; its older failure does not establish browser
connectivity at the time of failure or explain the latest input. The runtime
reader's communication-error handler
also covers normalization and host event processing, so the label alone does not
prove that the worker process died or that its pipe failed.

The handler currently retains a generic message with the exception class, but
does not write a communication-specific receipt with the original stack. For a
recurrence, first retain the local failure detail and timestamp, corresponding
runtime-event tail, matching host logs, process exit status if known, and the
running app/component revisions. If those do not identify the failed operation,
the next investigation needs a bounded private reader receipt distinguishing
read/decode/host-callback phases and stack locations without locals or payloads.
Share evidence privately after inspection;
do not paste raw conversation/provider payloads into a public issue. Confirm the
receipt belongs to the failed turn before connecting it to the latest input.
Without that evidence, keep the underlying disconnect investigation open and
avoid automatic restart or replay as a purported fix.
