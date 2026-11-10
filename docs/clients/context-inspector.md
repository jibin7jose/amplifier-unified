# Context inspector: retained host requests

The bundled web client's **Chat overview → Context inspector** opens a native
Canvas tab, not a modal or embedded page. This applies the November 7, 2026
direction to the clean-bar explorer selected in the November 6 design review.
It is a passive view of a bounded native-adapter observation, not a prompt editor.

The existing Canvas owns resizing, full-window focus, hide/reopen, document tabs
and dirty-view protection. Opening the inspector does not change the selected
chat or replace other saved documents. Only the live-view identity is saved as
an artifact; captured text remains in its existing private bounded store.
Container width, not device width, chooses a single column below 650px, a
master/detail split at intermediate widths, and a bounded navigation column with
an expanding reader above 1100px. Resizing keeps the selected request, category,
item and text page mounted, with no additional inspection reads.

The request selector and compact usage summary remain above the explorer.
On desktop, the item list and the reader each own the remaining pane height and
their own wheel/keyboard scroll. A long list cannot move a short reader out of
view or create an outer blank runway. Captured text has no nested fixed-height
scroll box. Narrow panes show either the list or the reader, with explicit
Back to items and restored item focus. A single-page item has no dead pagination.

Known exact model windows have a labeled reported-input/output-reserve comparison;
it is not a provider-wire count or fit guarantee. Unknown windows use a distinct
unknown-denominator summary, never a filled bar or inferred percentage. Detailed
source/reason evidence and composition are disclosed on demand; identical missing
causes are grouped without dropping their field names. Unknown, not captured,
not reported and explicitly automatic evidence retain their distinct meanings.
The native Canvas header stays one row at narrow widths. Its named document,
focus and close controls remain reachable; the selected tab is revealed on resize.
On narrow panes individual tab closing is in Open documents, not a duplicate
button that consumes the selected title's width. Other documents keep the same
Canvas selection and dirty-view guards.
The inspector places the shell-owned Details toggle beside its request selector,
instead of spending a second empty toolbar row on it; the shell still owns
Details focus, narrow overlay inertness and dismissal.
Below 650px, selecting an item condenses the request and capacity summary into
one expandable row. It keeps reported input and known/unknown model window visible;
expanding restores the selector, full capacity visualization and evidence.
Secondary item measurements disclose separately. Back restores the full overview,
while width changes and these disclosures retain the selected request and text page.
If reported input plus the captured reserve exceeds the known model window,
the comparison names the excess in text and its accessible description rather
than reporting zero remaining. Busy passive refresh retains keyboard focus on
the request controls and suppresses duplicate requests.

Item headings show a short source name. A compact label helps distinguish
same-name sources; the expandable provenance section retains the complete
captured label and a named Copy control. Full paths do not dominate the reader.

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
- Each limit has captured source/reason evidence. A missing budget distinguishes
  unsupported preflight, no retained scoped preflight, no usable provider decision,
  changed request/options, ambiguous candidates and bounded/unsupported attribution.
  `budgetBinding` records only a scoped candidate count (at most four), fixed
  fingerprint status/reason codes and association disposition. It never publishes
  fingerprints, request fields or an unmatched candidate's token values. A scoped
  preflight observation is not proof it belongs to this dispatch. An unavailable
  fingerprint on a copied request therefore retains **unproven association** and
  its observed bound/unsupported reason, not an invented equality or a claim
  that no preflight occurred. No scoped candidate means the dispatch fingerprint
  is not evaluated; the inspector does not traverse a request to match another
  provider or task's receipt. Older observations without these receipts say
  **not captured**, rather than inventing a historical reason from current config.
  `scopedPreflightCount` counts the legacy candidate pool only. Ticket attribution
  instead has an explicit `fitTicket.observed` marker and fixed status/reason;
  superseded, evicted or failed tickets say unavailable, not request changed.
  A fixed mounted context `max_tokens` attribute is captured as well as an explicit
  configuration value. No fixed value means automatic/unspecified, not zero.
  Explicit request output options are captured; a provider's model output maximum
  is never mislabeled as the request's output reservation.
- Provider metadata for a different default model cannot establish the selected
  model's window. An input allowance cannot establish the total window either:
  it may include independent provider safety/pricing limits. Providers that omit
  `context_window_tokens` from their existing budget decision need to supply that
  optional evidence themselves; Unified does not guess from model names or call
  a catalog/count endpoint to fill the gap.
- Host revalidation may copy an unchanged fitted request. A retained preflight
  follows that copy through the host fitted-owner ticket described below, or
  through an exact bounded fingerprint for paths without that owner. Exceeding
  the fallback bound returns no fingerprint, never a truncated-prefix digest.
  Ambiguous identical candidates are not matched by arrival order. Every dispatch
  retires its receipt; an identical concurrent or later request cannot borrow it.

The composition `bounded` flag is independent of fingerprint eligibility. Text
attribution has per-item, item-count, visit and scanned-character bounds; exact
binding traverses all request fields and normalized options with its own complete
character/node/depth/container budget. Provider input totals and the local text
estimate do not establish which bound fired. Only observed fixed reason codes
can explain a future capture. Existing captures are never backfilled.

### Passive final-fit budget attribution

Computer screenshot projection is prepared before `SurfaceProvider` calls its
existing delivery/fitting owner. When the independently released hook advertises
`computer_use.prepare_request`, the host retains its image-source validator with
the bounded prepared record. The hook's no-op projection preserves object identity;
already-projected blocks pass unchanged through its ordinary provider wrapper.
Ordinary CLI/Agent clients do not need the Unified integration.

Delivery requires both the host integration and the selected component graph.
The app lock records accepted OpenAI `c8104f327b9286bff49c589f804969a5a9d0ba9d`
and computer-use `951181f829cd6b3de7653a31bf8826f8a0b76456` for its affected
development/optional dependencies. The computer hook also comes from the selected
Foundation behavior/cache; updating the app's desktop-tool package alone does not
replace that hook. Existing qualified workers preserve their installed graph.
After app adoption, the normal included-component update must qualify and activate
the selected provider and hook/tool revisions before claiming these fixes are
loaded. Explicit pins, forks and excluded behaviors remain protected. Verify
installed graph/source receipts and then a naturally occurring call; an app version
or a moving `main` declaration is not loaded-dependency evidence.

Image validation is **execution evidence, not optional diagnostic evidence**. It
runs synchronously after metadata/admission and before provider entry, including
when Work, the inspector or a numeric ticket is absent. The current registered
projection owner, original capture/tool/file identity and exact fitted request and
options must still match. Refusal records the admitted call's error and never
silently retries or substitutes pixels. Its one complete current encoding is shared
with numeric attribution when present. Existing no-image diagnostic mismatches
still suppress attribution only. No skip registry or public request token is added.
Normal no-Work provider recovery revalidates the same prepared image source at
each real entry; it cannot reuse an exhausted numeric ticket. Concurrent/nested
admission cannot borrow the in-progress entry. Closing the dispatch clears its
request, provider, validator and fitted-value references, including inherited
contexts. The bundled inspector's `begin` callback is synchronous; an unexpected
async replacement is omitted, not awaited after final validation. Storage remains
background work and terminal persistence still drains its exact pending write.

The explicit cross-repository fixture is
`tests/fixtures/computer_projection_worker.py`. It requires the reviewed
computer-use hook/tool, Live/Boundary modules and OpenAI SDK/provider in an isolated
prepared environment. Run with `PYTHONPATH=tests:$PYTHONPATH python -m pytest
tests/fixtures/computer_projection_worker.py`; missing prerequisites fail rather
than skip. It generates PNGs through the canonical tool with a fake backend,
intercepts HTTP and forbids sockets. This is candidate-graph proof, not a live
desktop/vendor claim or permission to change installed dependencies. Ordinary
host guard regressions remain in `tests/test_request_projection.py`.

`SurfaceProvider` already owns a full fitted `ChatRequest` serialization. Its
private capture ticket shares that exact immutable string (no second prompt copy)
with a particular budget attempt and normalized dispatch options. Original request
and execution-task references are weak. Refitting supersedes the old ticket;
dispatch consumes it once and then releases its serialized reference.
The `app_guidance` construction site registers its exact recovery-wrapper edge.
Identity follows only those weak registered edges (maximum eight, cycle-safe)
and ExecutionEvents' immutable-provider sidecar map. It never follows arbitrary
plugin `original` attributes or equates names/models. Replacing any registered
edge invalidates the ticket. Modern nested and legacy flattened budget options
use the same effective merge: explicit keywords override nested options,
`context_estimate` is excluded, and type-sensitive bounds apply afterward.

`ExecutionEvents` claims the intended provider/execution/session/call synchronously
**before** its existing metadata and admission awaits. Nested or inherited tasks
cannot take that claim. After those same awaits and the unchanged Work admission
join, it performs at most one complete current encoding and compares it to the
fitted owner's value. Options are type-sensitive, JSON-like and bounded separately
(4,096 nodes, 65,536 characters, depth 16, containers 1,024); opaque values fail
closed. Only a verified, allowlisted decision reaches the inspector. A changed
suffix, nested field, options or superseded fit suppresses numeric attribution,
never execution, quota admission, refitting or retries.

This is not an admitted-call lease or new Core/Provider API. No guard is moved,
Work finalization is unchanged, and no Work obligation is needed. In particular,
Work's pre-admission digest is not borrowed: an admission callback can mutate the
request after that digest was made. The existing post-admission observation point
is synchronous through projection and provider entry; no new await is introduced.
Streaming claims at first iteration rather than iterator construction, and no
capture context survives an outward yield or grants a cross-task dispatch.
Each next/close has a temporary dispatch scope; the shared ticket's taken/valid
state, not that temporary scope alone, prevents reuse on later iterations/retries.

The cost exception is one linear encoding of the current owned request at final
admission, not one traversal per retained budget candidate and not a raised
inspector bound. `tests/test_provider_capture.py` measures encoding calls and
characters for small, >1M and >4M requests with four retained candidates.
`tests/test_context_inspection_binding.py` exercises the actual native
Core/Foundation/SelectedProvider/SurfaceProvider chain and preserves the
post-admission mutation falsifier, but its manually instrumented adapter omits
the production recovery wrapper. `tests/test_provider_capture_production.py`
qualifies actual `install_app_access` transformation through
`WorkRecoveringProvider`, SelectedProvider and initialized Core/Foundation,
including immutable providers, both budget signatures and session execution.
Earlier simplified-native evidence is not production-wiring qualification.
The installed native loop's streaming path adds live tool objects at dispatch
after budgeting without that option. That unsupported option set remains
unattributed; the ticket does not erase it to manufacture an unchanged request. The production
fixture distinguishes this from unchanged complete dispatch and directly
matched streaming calls. Changing the loop's option contract is separate work.
No request identity or serialized ticket is
stored in request metadata or diagnostic files. Paths without a fitted owner,
unsupported options and missing/ambiguous fallback evidence retain honest absence
dispositions; reading the inspector still never counts or calls a provider.
Ticket-owned budgets do not also enter the legacy fingerprint candidate pool:
otherwise a small identical nested call could borrow the same count through that
second path. This also avoids the redundant preflight fingerprint traversal for
the canonical fitted-owner path.

### Passive capture-stage evidence

Future headers may carry `captureDiagnostic`: a versioned, fixed-vocabulary
observation of purpose, dispatch, fit, take, claim and consume stages, plus a
candidate count bounded to four. `unspecified` purpose does not mean conversational;
unknown values remain unknown. Existing auxiliary/background exclusions are
unchanged. No labels, prompt text, private hashes, options or object addresses
enter this field, and older headers are not backfilled.

The fitting owner keeps at most four weak identity notes alongside its bounded
fit cache. A separate diagnostic dispatch scope can record an attempted entry
when no ticket exists, but cannot claim a ticket, supply a budget or affect
legacy fallback. It matches the exact request/task/provider; mismatched, inherited,
nested or repeated entries do not borrow the earlier fit details. Unsupported
weak identity remains unproven. A missing marker means **not observed**, never
proof that the surface owner was bypassed.

The call snapshots reached stages before metadata/admission awaits and adds the
existing consume outcome at the final synchronous observation point. No new
await, request scan, encoding, count call, persistent registry or execution
authority is introduced. Per-next streaming scopes expire before outward yields;
their shared diagnostic attempt is observed once. Diagnostic failures are
nonfatal and cannot suppress a working fallback budget.

This makes a future naturally occurring failure diagnosable; it does not repair
historical captures or claim that the unexplained 0.63 live numeric omission is
fixed. Synthetic Live/Boundary checks are not a substitute for that live evidence.

### Separate provider-owner proposal: exact model window

OpenAI's `_budget_decision` and legacy `request_budget` return a dictionary. The
current consumer validates its known required fields and tolerates additions;
Unified already allowlists optional `context_window_tokens`. The provider owner
can document and add that optional field from its effective request model/policy
without a Core dependency or another count call. Its meaning must be the actual
model context window, not a safety/pricing input cap. Unknown/custom endpoints
or an unsupported exact model must omit it rather than infer a default.

Provider tests should cover a selected model different from the mount default,
effective wire-model overrides, output/safety/long-context policy distinctions,
unknown/custom endpoints and both counted/legacy budget branches. This lane does
not change that provider, its dependency pin or its public contract. An additive
dictionary key is mechanically tolerated today; its semantic promise still
requires the provider owner's approval and evidence.

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
  late-response fencing. `chat-pages.jsx` and `work-overview.jsx` own entry points;
  the existing Canvas renderer host owns the native live tab.

This applies family vision principles 2–7/12, AP2/3/5, CB2/3, CS1–7, CX1/6–8 and
workspace context P3/4. It does not assert completion of the broader ACP/AHP migration.

## Focused checks

`tests/test_context_inspection.py` exercises the actual Core/Foundation final-request
and streaming boundaries with scripted providers, captured-file changes, adjacent
cycles, managed compaction, privacy, retained identities, failure, read scope and
bounded work. Existing provider-recording/execution/final-refit checks protect their
owners. `tests/web/context-inspector-browser.mjs` runs against canonical built
assets and production inspection responses, with synthetic captured input. It checks
entry/drilldown/reader, pagination, unknown/no-record/empty states, errors, cross-chat
late responses, keyboard, actual Canvas divider/focus transitions, retained other
documents and 320/390px and full-width light/dark layouts. Source inspection and scripted
checks are not live-provider or released-package qualification.

`tests/web/context-inspector-layout-browser.mjs` covers the 60-source
long-list/short-reader regression and paged long readers at actual wheel and
keyboard start/middle/end positions. It records whole viewport images and DOM
scroll geometry, narrow native-divider and full-window transitions, 320/390px
navigation, other Canvas documents and known/unknown window states. Pixel review
must identify the images and observer separately from these functional checks.
