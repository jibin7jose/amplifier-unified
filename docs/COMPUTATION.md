# Persistent computation

The shared `kernels.create/list/status/execute/interrupt/reset/close` actions
provide Python and Node state across cells in one conversation.
Cell output and results are durable operation evidence: use `operations.read` and
`operations.wait` with the returned operation ID and output cursor. Reading saved
kernels or cells never starts a worker, restores variables or replays code.

## Runtime and authority

The portable `amplifier_kernels` package has no app imports or direct subprocess
launcher. Its `Kernels(transport, observe, runtimes)` accepts trusted absolute
runtime paths, an authorized owned-process transport and a durable event sink.
The host adapter uses the existing managed bash start/list/write/wait/terminate
path through `RuntimeControls.invoke`; it neither evaluates submitted code in the
host interpreter nor imports Python packages to check them.

A mounted conversation worker and the opt-in managed bash implementation are
required. Trusted configuration must enable `managed_processes: true`,
`managed_stdin: true`, `safety_profile: unrestricted`, and no command allow/deny
restrictions or safety overrides. The passive process list advertises stdin
permission; a denied/unsupported profile never starts an interpreter. No action
can change this configuration or pick an arbitrary executable. Current producer
work is in [managed process PR22](https://github.com/microsoft/amplifier-module-tool-bash/pull/22).
No default bundle is changed here.

Every mutation invokes a mounted `compute` tool through the existing pre/post/error
hooks and coordinator permission path. The real `tool_input` includes cell code,
kernel ID and generation. The host snapshots and rechecks exact arguments after
approval, including in-place hook modifications. The underlying process calls
also retain their existing policy path. Approval waits do not hold the worker's
command lock. An old cell's cancel action cannot interrupt a later cell.

Code has the file and network authority of the approved unrestricted process;
this is not a filesystem or OS sandbox. Working directory and shell execution
policy come from the existing mounted tool. Python code executes only in an owned
Python child; Node's VM is a persistent namespace inside an owned Node child, not
a security boundary. Existing process groups and cleanup own their lifetimes.

Python uses the worker's exact `sys.executable`. Node resolves only absolute PATH
entries; a missing Node installation is explicitly unavailable. The startup
handshake records actual executable, version and PID; Python also records prefix
and installed metadata for selected public artifact packages. Node records Node
and V8 versions; workspace package inventory is not inferred from availability.
Node `require` resolves from the permitted working directory. No proprietary
artifact runtime, auto-installation or package import probe is involved. The
separate runtime discovery work provides broader host/worker dependency inventory.

## Cells, identity and lifecycle

`create(language)` returns a stable kernel ID and generation. `execute(kernelId,
generation, code, timeout)` returns a new stable cell operation ID after its
admission is saved and input is submitted; it does not wait for computation to
finish. `status` is a passive saved view. The kernel permits one active cell;
concurrent submissions fail rather than creating an implicit execution queue.
Successive calls preserve variables in that exact live process.

Python supports statements, a displayed final expression and awaited coroutines.
Unawaited asyncio tasks are cancelled before the cell finishes. Node supports
persistent top-level declarations and awaited Promise results; top-level `await`
syntax is not provided by this first runner (use a returned Promise/async function).
Background threads, escaped child processes and delayed Node tasks are outside
the cell execution contract. Output tagged to an earlier or absent cell invalidates
the protocol rather than being silently assigned to current work.

Cell code, its SHA256, exact runtime/generation, explicit question dependencies,
output and final result/error are private durable evidence. `questionIds` gates
only that dependent cell, before host admission and again before the actual cell
receipt is admitted after code approval. Only an exact recorded answer releases
the dependency; cancellation/superseding do not. Unrelated cells can proceed.

`interrupt` terminates the owned process; it does not promise to preserve variables.
`reset` requires a real observed exit result from the original process before
starting a fresh generation. Unknown termination never claims a clean reset and
never launches its replacement. `close` explicitly releases ownership. A stale
generation or foreign session cannot control a newer process. A denied stop stays
unconfirmed. The real process return code remains evidence where observed.

Host restart, lost ownership, failed/corrupt protocol or uncertain stdin delivery
never replays code. Saved cells remain readable; active receipts become outcome
unknown. Variable memory is not serialized or reconstructed. Create a new kernel
explicitly when the prior host is gone. External effects may have happened even
if their outcome is unknown.

## Bounds and evidence limits

A mount allows four kernels by default. Process lifetime is bounded to one hour;
cell deadlines are 1–300 seconds (30 default), after which interruption is requested
through the same policy path. Refused interruption does not become a false terminal
result. Output is bounded to 1 MB per cell before the journal's own retention;
result/error presentation is bounded to 4,000 characters with truncation metadata.
Code is limited to 16,000 UTF-8 bytes. Protocol packets and reads are bounded.

The producer streams encoded packets only through its existing bounded process
ring. A trusted host-only scope prevents those packets being archived a second
time as raw process output; decoded cell evidence is journaled once. Source
process identity remains in the cell/kernel metadata and owns lifetime cleanup.
There is no agent-settable observation suppression flag.

The cell event sink has a two-second deadline. Failed/late storage, local ring
expiry, forced reader cancellation, invalid encoding, bypassed protocol and output
retention are incomplete evidence. `outputComplete` stays false when original
output is missing, even after a successful cell result. A shielded shared finalizer
keeps cancellation of a reader from interrupting a durable terminal receipt halfway.
A completion receipt is runtime evidence, not proof that arbitrary external side
effects or generated artifacts are correct. Original binary output, PTY, native
Windows managed processes and reconnecting live variable memory after host death
remain unavailable. The operation journal's loss/uncertainty contract also applies.

## Validation

`pytest tests/test_kernels.py` exercises real Python and Node subprocesses when the
managed bash module is importable. It covers persistent variables, errors, reset,
interruption, exact code approval, generation/session boundaries, dependency
questions, bounded output and no replay. Host action tests also preserve the
selected conversation and draft while using the shared journal. These checks do
not use a live model provider and do not establish deployment or full Work bundle
acceptance. The bundled chat does not expose a separate Computation panel.
