# Context inspector: retained host requests

The bundled web client's **Chat overview → Context inspector** opens the clean-bar
category explorer selected in the November 6, 2026 design review. It is a passive
view of a bounded native-adapter observation, not a live prompt editor.

## Meaning and limits

- Default selection is the latest-started **completed foreground call retained by
  this adapter**. A newer retained running/failed attempt is separate. Each view
  binds a session, call ID, start time, provider and model. Background calls and
  child calls cannot replace the root's observation. Missing/expired observations
  are not reconstructed from saved history, current files or drafts.
- The observation boundary is the **host-final ChatRequest**, inside the existing
  fitted/revalidated provider wrapper and after call admission. It is not the
  provider's final wire serialization or hidden server state. Unsupported agents
  and optional context representations remain explicitly unavailable. No Core or
  provider protocol is required to depend on Unified.
- Provider-reported input usage and an existing provider-count receipt are separate
  from the local text heuristic: `ceil(Unicode code points / 4)` per known span.
  Category/item percentages use the known-text estimate, never the provider total.
  Unknown/redacted/omitted text, media, reasoning, provider state, tool-call
  arguments and serialization overhead are unscaled—not zero or an invented
  arithmetic remainder. Cached input still occupies context.
- Model window, request input allowance, configured context budget, output reserve
  and compaction threshold are independent. A window is shown only from an exact
  budget decision or provider metadata whose default model matches this call.
  Unknown does not mean unlimited. No fresh counting, model discovery or generation
  is triggered by opening, searching or refreshing the inspector.

## Privacy and bounded retention

With ordinary recording off, the adapter retains generic item positions,
category/size measurements and existing call metadata, **not text, paths, hashes
of content or tool names**. Recognizable secrets are excluded from attribution.
An explicit mounted provider `raw:false`/`raw_debug:false`, or disabled diagnostic
capture, suppresses composition observation. No setting is changed by this feature.

Sanitized verbatim copies require the existing provider-request recording preference
and the mounted provider's explicit recording opt-in. The existing redactor is reused;
reasoning/media/native opaque blocks are never retained by this projection. Recorded
wrapper paths are labels, not authenticated filesystem provenance. Text hashes bind
the captured redacted copy, not a later disk file. Current recording revocation gates
content reads and is rechecked after asynchronous file work.

The native diagnostic directory owns a four-request ring. Read eligibility is at
most 30 days (or the shorter diagnostic retention preference). Expired indexed
files are physically removed on the next capture, not by a quiet-session timer;
native session deletion owns the containing directory. Reads do not delete files.
This distinction is displayed in the inspector. Each snapshot has at most 256 items and
one million scanned/inspected text characters, at most 1,024 message/block/tool
visits (including empty representations), with at most 120,000 characters per item.
Selected content is returned in 6,000-character pages. A bounded four-write pending
set can omit observations under storage pressure without limiting model execution.
The UI reports the last **observed** identity; it does not assert complete coverage
of all provider calls. Snapshot loss cannot affect native history or recovery.

This inspector owns physical pruning only inside its own diagnostic subdirectory;
the diagnostic preference is otherwise “Index days” and does not delete shared CI
logs. A single write journal names the candidate and retired entries before body
creation. Failed writes reconcile against the atomic index; cleanup failure blocks
new snapshot growth until that exact journal settles. Physical storage can retain
the four-request ring plus one pending candidate during failure, never an unindexed
sequence of prompt copies. No full capture-directory scan or native-log deletion is used.

The sidebar makes no detail read. Opening reads only a small index and manifest;
selected content reads one bounded span. Neither path scans the transcript/events,
loads an agent, reassembles prompts or rereads current source files. Native session
removal owns the containing capture directory. Request pruning invalidates exact
IDs rather than substituting another request. Capture I/O is drained through the
native cleanup owner and does not add an awaited disk operation between final
request guards and provider entry.

## Read interface and ownership

`context.inspect` accepts `sessionId`, optional `callId`, optional `itemId`, and a
Unicode `offset`. An item read must name its exact call. The result includes the
boundary, retained request headers, selected header, coverage/method, bounded item
metadata or a selected text page. Agent reads are caller-scoped; authenticated UI
reads follow ordinary session identity/access. No browser argument chooses a path.

Canonical implementation owners:

- `execution_events.py` / `runtime_worker.py`: admission identity and optional native
  observation installation; `surface_delivery.py` remains the fitting owner.
- `context_inspection*.py`: bounded projection, diagnostic ring and authorized reads;
  never an execution context or recovery source.
- `context_continuity.py`: unchanged native checkpoint/recovery authority.
- `context-inspector.jsx`: selected A presentation, private selection/search and
  late-response fencing. `chat-pages.jsx` and `work-overview.jsx` own entry points.

This applies family vision principles 2–7/12, AP2/3/5, CB2/3, CS1–7, CX1/6–8 and
workspace context P3/4. It does not assert completion of the broader ACP/AHP migration.

## Focused checks

`tests/test_context_inspection.py` exercises the actual Core/Foundation final-request
and streaming boundaries with scripted providers, captured-file changes, adjacent
cycles, managed compaction, privacy, retained identities, failure, read scope and
bounded work. Existing provider-recording/execution/final-refit checks protect their
owners. `frontend/tests/context-inspector-browser.mjs` runs against canonical built
assets and production inspection responses, with synthetic captured input. It checks
entry/drilldown/reader, pagination, unknown/no-record/empty states, errors, cross-chat
late responses, keyboard and narrow/light/dark layouts. Source inspection and scripted
checks are not live-provider or released-package qualification.
