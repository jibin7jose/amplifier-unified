# Observed operations and durable output

Unified exposes one conversation-scoped view of managed processes, existing
Smart Tool receipts, and workers through `operations.list`, `operations.status`,
`operations.read`, `operations.wait`, and `operations.cancel`. Authorized clients
and agents also use `operations.submit`,
`operations.write`, and `operations.request` for explicit execution/input and
their durable admission receipts. Reading or waiting does not select
a conversation, change its draft, activate a runtime, or replay work.

## Ownership and admission

The host binds the real conversation identity at the worker bridge. A process
handle is authorized by its conversation, runtime session and mounted owner; an
OS PID or tool-supplied session identifier is not authority. Child runtimes stay
bound to the owning conversation while cancellation targets their exact mount.

Generic `runtime.control` / `tool.invoke` remains available for ordinary tools,
legacy Bash runs and passive process observations. Managed Bash mutations and
the computation tool require the shared operation/computation actions instead.
Agent invocation is bound to its calling conversation, and the host supplies the
actor to the normal approval hooks. A hook cannot redirect an ordinary Bash call
into a managed mutation. This restriction does not change direct model tool calls
inside their existing mounted-session tool dispatcher.

Managed process production requires a tool implementation that supports the
optional `operations.observe` capability and trusted `managed_processes: true`
configuration. This change does not enable it in default bundles. The host
registers a local callback, never a remote callback URL. The portable journal is
producer-neutral; the host currently accepts only tool-bash process events.

`operations.submit` starts a command through existing shell hooks and permissions.
`questionIds` binds only work depending on exact answers. The host checks them
before mounting, and the Bash `questions.admit` capability checks again after
approval, immediately before spawning. Pending/cancelled/superseded or foreign
questions do not release dependent work. Unrelated work remains independent.

`operations.write` binds the saved conversation, runtime, mounted owner and opaque
process identity. It uses the same approvals and checks the owner after hooks.
It cannot start or restore a missing process. Raw input additionally needs the
trusted unrestricted Bash stdin policy; a command allowlist is not stdin approval.

Both actions require a stable `requestId`. The journal commits an admission
receipt before dispatch; repeating the same request returns that receipt, while
changing its arguments or actor is rejected. Restart/transport interruption marks
unfinished admission as `outcome_unknown` without replay. `operations.request`
reads it and `operations.list` exposes recent receipts. An accepted request proves
tool admission, not process completion or successful external effects. Command
and stdin bodies are not stored in these admission receipts; their signature and
bounded admission metadata are retained; output stays in the existing journal.

Cancellation uses the existing `RuntimeControls.invoke` tool pre/post/error and
coordinator permission path, with actor, source and operation attribution. It
binds the exact `bash` terminate arguments and revalidates them after hooks,
including in-place modification. It does not require the conversation to become
idle. Approval and bridge waits run outside command locks. Cancellation never
reactivates a retired runtime and never treats a request as proof of termination.
Other receipt adapters remain read-only here and retain their existing controls.

## Durable library and observation contract

`amplifier_operations.OperationJournal` is a standard-library-only package included
in the wheel. It has no app or module imports. Its public calls are:

- `ingest(session_id, runtime_session_id, event, *, output_keep_days=None)` returns
  `(record, changed)`. A supplied lifetime is captured only on the first observation.
- `status(session_id, id)` and `list(session_id, limit=50, before=None)` read receipts.
- `read(session_id, id, cursor=0, max_bytes=16384)` reads a bounded output page.
- `recover(session_id=None, reason=...)` invalidates unresolved ownership.
- `output_expiry_candidates(now, *, after=None, limit=8)` reads an indexed page of
  due output records; `expire_output(session_id, id, expected_revision, now, *, keep_days=None)`
  atomically removes eligible chunks and records their unavailability. The host
  must check its current policy and consumer references before calling expiry.
- `close()` releases storage. Opening a library reader alone does not recover work.

The event envelope contains `schemaVersion: 1`, `eventId`, monotonic `sequence`,
`operationId`, `ownerId`, producer `source`, `kind`, `phase`, and `at`. Phases are
`started`, `output`, `state`, and `finished`. Output chunks have `cursor`,
`next_cursor`, `stream`, `text`, `source_bytes`, `binary_output_withheld` and
`encoding_loss`. The host deduplicates identical events and rejects conflicting
identity reuse, ownership changes and out-of-order delivery. Sequence and output
cursor gaps are retained as missing evidence. Commands and stdin are not in this
event stream; output itself may be sensitive.

Storage is a private SQLite WAL journal at `operations.sqlite3` with FULL
synchronization. Receipt and output changes commit together; output is not copied
into an additional unbounded event archive. Host backups use a consistent SQLite
snapshot, including output. Default output retention is 10 MB and 10,000 chunks
per operation; source event identities retain the latest 10,000 sequences. Output
reads are repeatable while retained, bounded to 128 chunks and 4–100 KB of source
bytes per page. `nextCursor`, `hasMore`, `earliestCursor`, `droppedOutputBytes`, and
`cursorGap` make pagination and expired evidence explicit. The host list returns
at most 100 recent operations; known IDs remain readable directly.

`operations.wait` uses revision changes and event notification, not a polling
loop. It returns on actual observation changes or its bounded timeout (up to
60 seconds). Clients carry the last revision and output cursor when reconnecting.

### Optional terminal output lifetime

Storage settings can explicitly enable 30-day output expiry for future operations,
or choose a duration from 1 to 3,650 days. This is off by default, including upgrades.
The host captures the chosen duration when it observes a new operation start;
legacy records and observations missing their start remain unclassified. The
deadline begins when this host observes confirmed `completed`, `failed`, or
`cancelled` evidence. Producer timestamps, reads and exact retries do not move it.
The captured `outputExpiresAt` is the earliest allowed deadline. A longer current
policy also protects existing output; a shorter policy cannot shorten its captured
lifetime. Disabling this choice or automatic cleanup stops further expiry,
including previously admitted output.

The existing hourly Storage maintenance pass checks at most eight due operations
through a journal index, yielding between individual atomic checks/deletions so
the app can process other work. Each current-task authority read is capped at 256 KiB;
it does not scan task history or start a worker. Live/preparing runtimes,
unfinished Work referencing the operation, unknown/interrupted outcomes, and
missing, malformed, changed or oversized current-task state preserve output.
Protected rows do not prevent the saved maintenance cursor from reaching later
eligible records. User or authorized Full/system callers can also run the same
saved policy through `storage.cleanup.run`, without approval per removal.

Expiry removes stdout/stderr chunks and increments the existing operation revision.
Readers receive `outputAvailability: "expired"`, `outputExpiredAt`,
`expiredOutputBytes`, zero retained chunks/bytes, and `outputComplete: false`.
The end cursor remains stable; earlier cursors report a gap. `streamComplete`
and `captureComplete` still describe the original observation. The final outcome,
exit code, kernel result/error, event fingerprints and admission/request identities
remain. Durable saved outputs and native history are separate owners and are not
removed. A subsequent validation cannot claim complete evidence from expired
output; an already saved validation receipt remains its original observation.

Chunk deletion and its expiry marker share one SQLite transaction. Failure rolls
both back; restart and duplicate observations cannot regenerate the chunks or
replay the command. Cleanup reports logical output bytes removed separately from
filesystem bytes freed; SQLite may retain reusable pages. There is no automatic
replay or restoration of expired output.

## Truthful completion and limitations

Process states distinguish `running`, `cancel_requested`, `completed`, `failed`,
`cancelled`, and `outcome_unknown`. Terminal process success/failure/cancellation
requires an observed exit code. On app restart or loss of the owning runtime,
unresolved active receipts become `outcome_unknown`, their captured output stays
readable, and their controls become unavailable. No command is replayed. A real
final event from the same owner can settle an unknown receipt; a reconstructed
runtime cannot. External side effects may have happened even when outcome is
unknown. This is durable observation, not a broker that keeps a process alive
through host death.

Output completeness has three separate fields:

- `streamComplete`: the producer drained its streams.
- `captureComplete`: every observed original byte is represented without source
  sequence gaps, observer loss, binary withholding or lossy text decoding.
- `outputComplete`: streams drained, capture is complete, and no retained original
  output expired. This stays false after exit if any original evidence is missing.

The optional producer uses a bounded mailbox and a deadline for observer calls.
It never blocks subprocess draining on storage. Under backpressure, observer
failure, timeout, binary guarding, invalid UTF-8, or retention expiry, output can
be partial. Loss markers are evidence of loss, not a substitute for the missing
bytes. Availability of the callback does not guarantee durable capture. A host
crash can occur before an event commits; surviving receipts cannot prove an
unobserved external outcome. Normal process completion with a complete local
ring is likewise not proof that the host journal captured everything.

Smart Tool receipts and worker jobs are projections of their existing stores,
not duplicate execution registries. Their original result/error/report fields
remain the evidence; output completeness is unknown rather than inferred from
terminal status. An idle worker is waiting for input, not completed. Large output
references can point to an actual saved operation ID and cursor via the shared
read action; arbitrary historic tool payloads are not silently archived here.

POSIX terminals require trusted `managed_pty=true` plus per-request `pty=true`.
They use an actual controlling terminal and merge stdout/stderr. Terminal EOF is
canonical EOF, not a pipe half-close; raw-mode EOF is explicitly unavailable.
Terminal sizing, native Windows managed execution, and reconnecting to a live
process after host death are unavailable. Raw interpreter input still needs
an explicit trusted mount policy; command allowlists do not authorize stdin.
Full original binary output storage and guaranteed lossless delivery are not
provided by this text observer. There is no automatic retry of input, execution,
or external side effects.

## Validation

Run the focused Python suite with `pytest tests/test_operations.py`. If the
managed bash module is importable, it also exercises a real subprocess and durable
output beyond its local ring; otherwise that optional integration test skips.
`tests/test_operation_output_retention.py` checks prospective policy, protected
authority, atomic expiry, exact retries, bounded pages and inter-candidate changes.
`node tests/web/storage-cleanup-settings-browser.mjs` checks the shipped
Storage controls. `node tests/web/operation-output-retention-browser.mjs`
drives a real Python cell through the public HTTP actions, expires its journal
stdout and verifies retained result/outcome and no replay. It does not claim a
mounted Operations/Computation panel or a live-provider conversation.

`node tests/web/operations-http-browser.mjs` checks authenticated HTTP
observation, real command submission/input, exact retries and one side effect
across browser reload, while preserving the selected conversation and draft.
It uses an isolated fixture and the production build; the bundled chat has no
separate Operations panel. These checks do not claim a live provider conversation
or full Work bundle composition has been deployed.

Additional host receipt stores can register a read-only adapter with
`service.operations.register_source(name, list_records, read_record)`. Callbacks
receive the bound session ID (and exact prefixed ID for read) and return common
operation records. Identity/session mismatches are rejected. The original store
owns execution and evidence and calls `operations.notify()` when it changes.
This registration is a trusted host seam, not an agent-defined callback.
