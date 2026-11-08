# Managed runner qualification

This change extends workspace test environments with supported setup, prepared
dependencies and durable queued execution. It does not deploy a production host
or establish native desktop-platform support.

## Observed acceptance

### Smart Tool migration check (2026-11-08)

The new setup default is Docker, not a conversion of saved installations. The
bootstrap remains reviewed core `5235833098d9a630791452a46558d92f58af0389`;
its app-owned moving-source policy upgrades through the existing qualifier.
Static historical acceptance was not extended to a new commit.

The opt-in `scripts/qualify_managed_runner.py` drives those same setup/update
owners from an installed candidate and an empty private app home. It requires
the already-observed local daemon ID. It never installs a daemon or edits live
app settings. Retain the same private directory on failure or uncertainty;
re-entry refuses unfinished installation and observes the original qualification
owner instead of replacing it. Its small persistence adapter is test-only;
the actual package installation, admission, snapshot, durable job, isolation
inspection and cleanup are the production implementations.

On native Linux ARM64 Spark-2, the installed candidate qualified exact Smart Tool
`88d9d84149eee914372c1ca8d68e9eb9f4ba3290` (0.6.9), Python 3.13.12,
Compose 5.2.0. Setup's disposable offline image probe completed first. The
separate dynamic qualification used one CPU/512 MiB, no host mounts, an offline
unprivileged workload, committed and uncommitted synthetic source, a durable
terminal job, verified artifact export, stop and confirmed removal.

Exact update `57d4eaf7167fad75ffb95c2f668f6f2a` retained runtime SHA-256
`807dc85f8d51a073373bb951f3c3b36b39408119a1c155aaa9bcdccc35363320`
over 4,488 installed files. Guest
`dtu-op-3d4469980f006b54259a25f59f05ad59` was removed, its allocation retired,
and repeat selection observation performed no guest/check replay. The old core
configuration remained valid. This is target-local installed-library proof, not
live app adoption, remote Docker registration, native desktop qualification or a
fresh Incus lifecycle. Existing Incus and unknown-owner controls remain separate
deterministic tests; no historical Incus resource was accessed or modified.

Focused setup/core/update/configuration/worker/capacity checks passed (248 tests,
one hosted-only real-Docker smoke skipped); four qualifier storage/no-replay
controls passed. Recovery and queue controls passed 233 tests. The 96 frontend
component tests and the built queued-check browser lane passed, including the
Docker recommendation, explicit Incus choice and retained UNKNOWN on reopen.
The browser uses a synthetic private backend, not native lifecycle evidence.
Its first attempt exposed the fixture's stale setup callback signature; accepting
the production `origin`/`authority` arguments repaired that fixture. The separate
first component-test invocation used the wrong working directory; the corrected
frontend-directory invocation passed. Both failures remain retained evidence.

Follow-up review found two test-driver boundary defects, not an observed failure
of that native run: a second setup discovery could choose a different daemon,
and concurrent CLI invocations could race staging in one private home. The
driver now checks the actual selected target before native setup, its retained
setup and prepared/final qualification identities, and holds a nonblocking
same-home process lock through final persistence. A busy loser changes no JSON
journal. Deterministic regressions reproduce both on the original driver
(including two staging dispatches), then pass on the corrected driver.
The production runtime and built frontend are unchanged by this correction.
The recorded native lifecycle remains original-run evidence; it was not replayed.

### Earlier managed-runner acceptance

- Linux ARM64, Docker: setup through the shared action installed the qualified
  deterministic tool, verified the exact base image with an offline Python
  probe, removed the probe and saved the private configuration.
- Two distinct root chats submitted jobs through the shared queue. Closing and
  reopening the host while one command ran retained its single execution;
  cancelling the other stopped only that job. Both exported evidence and removed
  their environments. This was repeated on the final release-integrated source.
- A locked Python project prepared and ran pytest offline. Root project package
  metadata was available. Cancelling a live dependency builder stopped it and
  removed its owned resources while retaining evidence.
- The browser profile prepared locked Python/Node dependencies, launched
  Chromium headlessly and ran the unchanged 100-trial Linux X11 coexistence
  workload. It completed in 584.8 seconds: 95 detections, zero false positives,
  release on all detected halts, all existing assertions passed. The queue
  retained source and complete artifact output, then removed the guest and its
  prepared image. No assertion was relaxed and the benchmark was not retried.
- Linux ARM64, Incus: setup used a private project, bridge and default-profile
  root disk on an existing pool. Its offline probe passed and was removed.
  Bare queued checks and a prepared Python/pytest queued check passed.
- Browser controls: workspace Details entry, explicit setup, profile selection,
  keyboard submission, queued cancellation, reload, bounded output paging,
  narrow layout and unknown-result retention passed. No JavaScript errors were
  observed. Interactive computer use also exercised profile selection, queuing
  and reading a completed result. No chat-header control was added.

## Source qualification and limits

The release-integrated source passed 154 focused lifecycle tests, 25 setup
tests and 18 frontend component tests. Additional transport/capacity and Incus
launcher suites passed (72 and 45 tests). The prepared-profile suite passed
29 tests. Eight supervisor process tests were separately run in a root-owned
Linux test environment; ordinary unprivileged pytest correctly skips them.
The frontend was rebuilt with the server changes.

The long benchmark ran on the frozen integration candidate before the final
observation-cost and Incus setup fixes. Its guest dependency recipe, profile
definitions and transport match the final source. The final observer changes
were qualified by focused tests and the repeated actual Docker restart/cancel
run; the complete long benchmark was not repeated on that later tree.

UI fixtures use a controlled backend. Actual Docker and Incus acceptance are
separate evidence. Completion notification guards have source-test coverage;
no real provider completion/resumption was invoked. Headless Chromium inside a
guest does not prove an interactive preview route, physical input behavior or
native macOS/Windows/WSL setup. Memory admission observes free headroom and caps
builders; it does not reserve memory against unrelated host activity.

Read-only discovery needs no setup approval. Installing/configuring the runner
is a distinct host effect: a direct user action or the narrow agent
`test_host_setup` grant authorizes it. Cleanup must retain ownership whenever
the guest, builder, prepared image or evidence export has an uncertain outcome.
