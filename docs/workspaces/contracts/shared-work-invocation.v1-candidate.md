# Shared Work invocation association — v1 candidate

Status: **first producer/reader implementation, qualification pending**. The
test-first source and its failed absent-API baseline remain preserved. Based on
the preserved Shared Work backend checkpoint, this covers commissioning, not universal
runtime enforcement. It does not replace the accepted Work contract or grant any
operation permission. The bundled consumer and host must adopt one exact revision.

## Boundary and owners

The host must distinguish the complete discoverable Work list from the smaller set
that governs an actual invocation. A title, source excerpt, model-supplied Work ID,
or ownership of many items cannot establish that association.

- Backend owns canonical association resolution, service actions and host admission
  facts. Existing `commands` receipts remain the store; no new database or tracker.
- Continuity owns task projections, coordination delivery and the narrowly scoped
  app-access/request adapters. It observes delivered content and checks the actual
  tool and provider boundaries. A helper returning true is not runtime acceptance.
- UI continues against its frozen Work DTO. This proposal changes no existing Work
  list/read/publication input or output; UI does not need an active worker to browse.

## 1. First producer: commissioned initial input

Add optional `workReferences` to the **existing `coordination.create`** input:

```json
{
  "workReferences": [{
    "workId": "work:synthetic",
    "expectedRevision": 2,
    "promiseRevision": 1,
    "candidate": "exact-candidate-id",
    "role": "executor"
  }]
}
```

The array has 1–8 distinct Work IDs. Each object permits only those five fields;
`role` is executor or reviewer. IDs retain the existing Work bounds. No caller
supplies an actor, target root, input ID, generation, claim, grant, observation token
or verified flag. Omitting the field preserves ordinary commissioning behavior;
it does **not** assert that the new root has no obligations.

Before recording the creation plan, validate every reference through existing
workspace, actual root/principal, Work owner, open accepted commitment, exact
record/promise/candidate and current task/Stop/assignment guards. The current Work
candidate must already be recorded; this field cannot silently choose or change it.
A root merely linked to somebody else's Work cannot commission on the owner's behalf.

For a new item, `workspace.work.create` returns `candidate: null`: its `intentKey`
deduplicates capture and is not a candidate. Before commissioning, the accountable
owner calls `workspace.work.link` at that item's exact revision with its own
`linkedSessionId`, `role: "executor"`, and the actual candidate identity (for code,
the source commit). That executor link establishes the candidate without starting
work. Build `workReferences` from the returned item's `id`, `revision`,
`promiseRevision`, and `candidate`. A reviewer link does not select a candidate.
Re-read the item if it has changed; never substitute a creation command or request
ID for its saved candidate. This is the existing link lifecycle, not a new
commissioning approval or a claim that the candidate passed review.

Malformed reference schema refuses with status 400; missing Work with 404;
borrowed root/principal or nonowner authority with 403; changed record, promise,
candidate or generation with 409. Each refusal names its actual cause. A missing
action or unsupported schema is not evidence that a semantic guard worked.

The existing creation owner derives the child identity and `initialInputId`. It
retains the proposed references in its canonical creation receipt, covered by the
same command fingerprint. Changed retries refuse. The normal root-creation receipt,
not a directory/title, proves that the child exists.
An exact retry preserves the Work revision, link-event identities, association
bytes and input count. Changed references under that same request ID refuse with
409 before any new record, link or input is retained.

Immediately before committing the initial `coordination.send`, revalidate the
references and the saved creation authority after awaits. The host atomically:

1. Retains the ordinary initial-input receipt.
2. Records the explicit child Work links and immutable link events.
3. Seals `workAssociation` on the existing creation receipt and adds only its exact
   reference to the initial-input receipt.

This includes the awaited `session.create` return after its root receipt commits.
A promise correction or Stop while that return is held must suppress initial
association/send; an otherwise identical unchanged control must still proceed.

The source root remains the Work owner. Commissioning is the explicit execution
operation already requested; a subsequent Work read never creates a child, claims a
task or enrolls anyone. The host uses a creation-owned transaction/helper, not a
forged public Work action with a fabricated agent generation. A partial transaction
must expose neither an admitted association nor an executable initial input.

`workAssociation` contains version 1, the creation request ID, initial input ID,
source/target root IDs, workspace ID, capture time, separate source-task and target-task
bindings where recorded, and the exact Work references plus their retained link-event
IDs. It is retained **inside** the creation receipt, not a second mutable authority.
The input receipt stores `workAssociationRef` naming that creation receipt/version.
Missing or changed links/receipts remain unavailable; they are never reconstructed
from brief prose. No task claim is created or reassigned by this producer. Existing
claim-bearing links must retain and revalidate their actual assignment/task/claim IDs.
The first new-child path does not pretend an unclaimed child already holds a claim.

## 2. Current projection and exact reads

The existing `coordination.current` bridge adds:

```text
work:
  available: true | false
  discovery: existing workspace.work.context page, or explicit unavailable
  required:
    status: available | unavailable
    reason: null | explicit cause
    version: opaque current-association fingerprint, or null
    rootSessionId, generationId
    inputIds, associationRequestIds
    workCount
    requirementCount
    requirementsRef: workspace.work.invocation exact read handle
    coverage: {complete, supportedInputIds, unsupportedInputs:[{inputId,reason}],
               reason?, missingInputIds?, deliveredInputCount?, projectedInputCount?}
    effectAuthorized: false
```

`discovery` continues to expose all open owned/contributing items with bounded
paging and incomplete historical coverage. `required` derives only from the actual
transport principal/generation and the complete **delivered** input-ID set in host
observations, following their exact canonical creation/input associations. Queued,
unknown, merely accepted-but-not-delivered or terminal input cannot stand in for it.

Limit this first producer to retained commissioned initial inputs. Direct human
turns, independent roots, untyped follow-ups, steering and scheduled input are
explicitly unqualified here. An already admitted native child inherits the exact
original generation and delivered input set captured before its preparation
starts. The live native child run and current worker process authenticate that
read/effect-check scope; a normal parent turn finish or newer parent turn does not
cancel it. Stop, execution/configuration changes, worker release, child termination
and current task/assignment controls still invalidate the child. An agent waiting
on a dependency does not itself revoke the child doing that dependency. For
commissioned Work, this requires contiguous canonical agent-only change rows from
the sealed active task, changing only its dependency-block fields; missing history,
new tasks, changed instructions, human blocks and pause/resume invalidate that
exception. The original sealed task binding remains the coverage binding. Children
retain the original required-content version and coverage checks, and cannot use
this scope as a root principal for Work mutations or peer control. Unsupported
input lineage remains unsupported in the child. A mixed set with unsupported
input lineage returns unavailable with coverage; it is not a manufactured empty set
or permission to execute. This proposal does not silently block or exempt all ordinary
unrelated work: the existing policy remains authoritative, and no claim of Work-aware
effect qualification is made outside the supported producer.
For mixed host-observed delivered input, retain every input ID, identify supported
IDs and each unsupported cause (`untyped_followup` for an ordinary untyped queued
follow-up), and set `complete:false`, `status:unavailable`, `version:null`.
Never drop the unsupported subset and qualify the remaining association alone.
The transport projection must equal that host-observed set, not merely be its
subset. A shortened projection is unavailable with `input_projection_incomplete`
and the missing input IDs. The bundled worker supplies the complete canonical
native input set regardless of count; neither the display tail nor content-page
budgets may truncate it or replace it with an empty set. Per-input identifiers
and diagnostics remain strictly typed and bounded in individual length. Input
identity metadata is accounted separately from discovery and detail-page content;
retained read-cache memory still accounts for every actual byte. These observations
authorize no effect. Unsupported lineage stays unavailable with its complete
identity and reason, and does not erase independent discovery.

The fingerprint binds the actual root/generation/input association, current Work
record and promise revisions, source identities/digests, owner/link/Stop revisions,
and any real task/claim authority it uses. `continuationEpoch` is a separate task
admission identity, never a substitute for current constraints. Missing legacy
association, corrupt receipts, unavailable owners or stale claims have explicit causes.
Reads do not hydrate history, initialize a runtime or contact peers.

Proposed additive shared reads:

| Action | Input | Result |
| --- | --- | --- |
| `workspace.work.invocation` | `sessionId`, optional `expectedVersion`, `limit` 1–20, opaque `after` | Current association header and a bounded page of flattened required-detail references; Work count and requirement count stay separate. Cursor binds root/generation/version. |
| `workspace.work.promise` | `workspaceId`, `id`, exact `expectedRevision` and `expectedPromiseRevision`, `offset` default 0, `limit` 1–2000 | A Unicode page of canonical JSON for current interpretation, criteria, constraints, commitment and promise/source pointers; full-value SHA-256, total, offset, nextOffset and exact revisions. No history bodies. |

A required-detail manifest names the current promise page and exact retained source
reads. Reuse `workspace.work.source` for immutable source bodies. Retain the original
source and every source-bearing promise correction through the current promise;
reading only the newest correction must not imply incorporation of earlier conditions.
No model summary, timestamp or last-N rule may silently mark a source superseded.
Any later supported explicit incorporation policy needs its own retained evidence.
Manifest paging and source paging are both required when present; a complete metadata
page is not a complete-content observation.

Discovery and detail content pages are capped at 12 KiB UTF-8, apart from the
complete validated input-identity metadata, and make progress through explicit
continuations. Required source bodies remain on-demand, not automatically injected
into every provider request. A stale current revision refuses rather than mixing
old and new pages. Immutable source pages remain readable. Strict Ask assurance
rechecks currentness before a dependent effect; the Full policy below permits
ordinary owner admission without claiming that assurance.

This first resolver inspects the complete delivered input set, at most eight
distinct Work items and 4096 source metadata rows per item. Overflow is explicit unavailable
coverage, never a silently shortened relevant set or permission to act. It reads
source metadata only; bodies stay in the existing source reader. Missing earlier
promise revisions also make coverage unavailable. The limits do not erase Work or
change the existing independent list/history/source readers, and do not revoke
independently valid Full authority.

Exact proposed result fields, inside the ordinary `{accepted:true,result:...}`
shared-action envelope:

```text
workspace.work.invocation result:
  status, reason, version, rootSessionId, generationId
  inputIds, associationRequestIds, workCount, requirementCount
  items: [{kind: promise|source, workId, revision, promiseRevision,
           candidate, ownerSessionId, contentSha256, readRef}]
  truncated, nextAfter, observedAt, coverage, effectAuthorized:false

workspace.work.promise result:
  workspaceId, id, revision, promiseRevision, originalSourceRef, promiseEventRef
  text, offset, total, nextOffset, truncated, sha256
```

`readRef` is the exact host action/arguments for the promise page or the existing
immutable source reader; clients must not construct replacement handles. Source
rows name their original immutable event in `readRef.args.sourceId`. `total` and
offsets count Unicode code points, while `sha256` covers the complete UTF-8 value.
The promise JSON has `interpretation`, `criteria`, `constraints`, `commitment`,
`promiseRevision`, `originalSourceRef` and `promiseEventRef`; it excludes mutable
report/history bodies. Serialize exactly those keys as compact JSON with sorted
object keys, preserved array order and Unicode characters (Python
`json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))`).
Neither read accepts an assertion that it was consumed.
An unavailable invocation has `version:null`, an explicit reason/coverage and no
claimed complete relevant set. It must not serialize a false zero-obligation verdict.

Task/claim fields on the retained association are not interchangeable:
`sourceTaskBinding` and `targetTaskBinding` each retain their own task ID, revision
and continuation epoch when recorded; an absent binding is null with coverage.
`claimRef`, when a supported existing association actually uses one, retains the
assignment ID, task ID, claim ID, holder and claim revision from that owner. The
first new-child producer does not accept or invent claimRef as caller input.

## 3. Service facts versus runtime enforcement

### Standing Full authority and Work assurance

Operator-selected Full access accepts the risk of acting with partial or inferred
Work context. Missing, compacted or unavailable fitted-content proof, unsupported
follow-up associations, and coverage limits do not withdraw independently valid
Full authority. The shared Work policy checks the current saved mode and the
existing authenticated native generation and Stop boundary, including trusted
descendants without changing their actual principal. It then leaves every
supported action to its ordinary owner, without a Work-specific action allowlist.
For a current native child, that generation and input set come from the existing
live child-execution owner; normal parent completion or a newer parent generation
does not renew or revoke the child's original assignment. Invalid supplied child
provenance cannot fall back to the parent's binding. Root-only consumers remain strict.
No fresh human input, additional grant or copied permission flag is required.

Coverage observations remain honest: allowing Full work neither marks required
content consumed nor supplies a required-version proof. `effectAuthorized:false`
remains mandatory. Work mutations and claims about a particular promise, candidate,
independent check or publication retain their own revision and provenance checks.
Actual Stop/revocation, execution and resource ownership, required factual inputs,
exact retries and unknown-effect recovery remain with their existing owners.
Provider preparation applies the same current Full policy through the worker's
existing local policy reader, without another host request. It preserves fitted
bytes and the captured native generation/input/Stop scope while allowing stale
or unavailable Work-version assurance. Skipping that assurance leaves coverage
unavailable, never complete. Native send admission rechecks current Full when
preparation relied on it.
An unsent request refit uses that same policy. Reusing its measured observation
must not introduce an additional Work-assurance gate under Full. Refitting still
retains the same native input/generation/Stop scope, measures the actual revised
bytes, and never retries a sent or uncertain provider request. Ask revalidates
its required Work version after all awaited preparation and measurement.
An actual native call already present in the adapter's consumed-call set cannot
execute again, independently of coverage completeness. A new native call may
inspect or exactly retry an app command through that command's canonical receipt.
Consumed IDs survive Work observation recovery and proof-retention overflow.
They are retained for the actual native generation and cleared when that native
generation or Stop epoch changes; their count does not impose a tool permission
ceiling. Bounded request/proof observations remain separate from this execution
evidence. A very long generation therefore retains one ID per consumed call.
This policy does not qualify every consumer for descendants or establish that
every delayed external effect already has complete cancellation coverage.

Ask when needed retains the strict Work preconditions described below. Work
readers and continuity observations use the same truthful coverage contract in
both modes; unavailable is not an empty or complete required set.

Proposed private bridge `work.invocation.revalidate {version}` compares the current
association and required content identities. It returns `current`, the current
required header and **`effectAuthorized:false`**. It performs no effect and accepts
no model-provided list of Work IDs or successful-read assertions. Root/child/generation
checks come from the actual worker envelope. This is a host fact for the continuity
adapter, not a public execution grant or a standalone safety check.

The private worker bridge `work.effect.check` accepts exactly `toolName`,
`toolInput` and the actual executing `toolCallId`. The authenticated transport
supplies `_workOrigin`; a public/model witness is not accepted. It returns
`applicable`, `allowed`, `reason`, `requiredVersion` and `effectAuthorized:false`.
`allowed` means that current Full authority permits ordinary owner admission,
the strict Ask Work precondition is satisfied, or the operation is an explicit
exact-read/safe-reporting case. It does not replace
ordinary permission, Stop, budget or effect-owner admission, and is not a lease
that survives another await. Unknown commissioned lineage is not an Ask exemption
and does not erase independently valid Full authority.
The runtime final-invoke wrapper and each shared-action mutation owner must
consume this contract at their actual boundary; the endpoint alone does not
establish production enforcement. Tests of its trusted-transport fixture are
service checks, not native origin or effect-execution proof.

In Ask, the bounded host integration rechecks covered Work mutations, chat
pinning and peer-only `notify` at their synchronous commit boundary under the
service lock. Other Work-dependent shared-action mutations explicitly refuse
with `work_effect_owner_unavailable`; a queued job or action with later effects
must first retain and recheck its own binding. This is an explicit implementation
limit, not completion of every accepted effect path. Unrelated roots keep ordinary
policy. Exact reads, receipt inspection, own-request cancellation and attributed
Work reports remain available through their existing permission owners.
The worker-local Stop counter is not compared with the persistent host interruption
counter: the former is rechecked by the worker, the latter by canonical Work
currentness. A guarded programmatic container conveys no permission to its inner
calls; the worker verifies the installed tool and dispatch objects before stamping
its private flag. Generic streaming and unmeasured native steering remain
unqualified. Native controlled-wire and nested-dispatch checks are not live-provider
or arbitrary tools' internal-await qualification.

To certify complete Work coverage, the continuity adapter must independently
establish all of the following at the actual runtime boundary. Ask requires that
assurance before dependent effects; Full does not:

- Every required page was genuinely delivered, contiguous, exact and available in
  the fitted current request. Tool success or a raw pre-truncation object is not
  proof of delivery. Copied IDs, user text and a `tool_exec` projection that omits
  the body cannot acknowledge it.
- The observation belongs to the actual invocation/generation, not a peer or a
  previous compacted context. Compaction/reopen invalidates it unless an existing
  supported retained-content proof establishes continued availability.
- After awaits, the required set and task/claim/Stop/permission/budget authority are
  still current. Exact reads, operation reconciliation and safe reporting remain
  possible when a dependent effect is denied.
- Nested and batched tool calls reach the same guard. Unknown provenance fails
  visibly; no fake nested-call receipt may be used to demonstrate enforcement.
- Budget measurement and complete/stream use the same fitted bytes. A changed
  authority boundary causes supported refusal/refit, never unmeasured substitution.

The exact observation hook and proof of post-truncation delivery are adapter work,
not invented by this service contract. Without that proof the service tests below
cannot qualify actual effect admission.

## 4. Historical service discriminators and initial baseline

This section records the initial service-only increment, not current qualification.
At that checkpoint, `tests/test_work_invocation.py` proposed synthetic **real AppService/receipt/bridge**
checks using the repository's existing commissioning and transport fixtures. No
successful invocation association, claim or check receipt is manually inserted to
make a positive case pass. Fault cases may deliberately corrupt retained state and
must be labelled as faults. The retained test-first baseline executed all 26
invocation cases: 25 failed at the absent outer `workReferences` schema and one at
the absent `work` projection. Four added publication controls and the previous
250-case backend selection passed. That baseline did not reach the deeper invocation
assertions. The first implementation adds five rollback, bounded multi-item and
missing/corrupt-lineage controls; their changed-candidate results were then pending.
These tests neither emulate a native worker nor demonstrate dependent tool execution.

| Required boundary case | Authored service discriminator | Still required from continuity/runtime owner |
| --- | --- | --- |
| One of two owned items governs an invocation | Atomic commissioning association and required set exclude the unrelated item; discovery remains complete. | Actual dependent tool uses only the bound set. |
| Forged/stale identity cannot select authority | Wrong principal/generation/input/reference and stale Work refuse or become unavailable without effects. | Native tool:pre and nested/batch provenance controls. |
| Large Unicode current scope needs full pages | Promise pages reconstruct exact JSON/hash; source manifest links remain explicit; changed revision refuses. | Partial/truncated reads cannot acknowledge; complete current read permits only the authorized effect. |
| Correction/Stop during a boundary invalidates it | Revalidate detects Work revision/owner/Stop changes; no receipt or runtime input replay. | Correction between measured request and complete/stream aborts/refits without different unmeasured bytes. |
| Restart/compaction/missing state are not empty authority | Cold retained receipts survive; missing/legacy associations report unavailable without hydration or replay. | Real compaction/reopen invalidates observations and missing/corrupt native state fails closed. |

Publication's existing authority guard additionally needs strict unlinked, stale-promise
and stale-candidate reporter controls. That is test coverage, not a claimed current
product defect. The frozen backend and all earlier passing/failing evidence remain
unchanged on their original branch. That increment changed only the commissioning
owner, Work resolver/read projections and their service registration, plus scoped
tests and this coverage record. Existing UI DTOs and continuity adapters were
unchanged in that increment. No paid-model probe, provider configuration,
publication, main merge or deployment was established by that service baseline.

### Subsequent bounded host/native qualification

Checkpoint `6ac9611ddd85205e2781fe2e17f3bc950feee030` joins the Work UI, cold
continuity and native adapter `83f6f39c09b8badcd38e2afa60bd436910253e4e` to actual
Worker/AppService `work.effect.check` and the final host mutation boundary.
Controlled native SDK early-tool calls and real nested JavaScript dispatch reach
the actual host pin owner, not a synthetic policy endpoint. Missing/opaque
coverage, same-batch reads, Stop and reused origins refuse before effects. The
reused-origin test requires explicit `work_coverage_unavailable` before ordinary
receipt deduplication; it does not identify which internal invalidation made
the reused witness incomplete.

Retained source and fresh-installed evidence is attributed to its actual inputs:
466 cases per mode at `56615e9bf2560e7dc5dc9d714410bb1129c3e078`; 49 changed cases
per mode at `3bf98ec9821d7226a4a9089544262ea4d7ce0948`; and nine corrected cases
per mode at final `6ac9611d`. The effective unique selection is **476 = 427
unchanged exact-input cases reused + 49 changed cases**. The final nine replace
the earlier nine; they are not nine additional unique cases. This is not a fresh
476-case execution at the final SHA. Original failures and failed controls remain
historical evidence, including the original 26-case red baseline.

That checkpoint also has source and installed three-client Work journeys covering
cold tasks, currentness, dirty Canvas/Settings and Work Back/Forward. Its 802 wheel
payloads match final source and its 311 static files match build
`0d135f7c3908ed28`. The retained qualification packet is `main022-result.json`
(SHA-256 `f41acd32f661e9b702c8865d5a2764150d9fee42e9713ec46c1ab868d3e1c40c`);
raw identities, exact-input reuse and review dispositions accompany that packet.
These are checkpoint-specific facts, not qualification of the later frozen
`4351124b443f62cad2d3034d784727852022f10f` integration.

`effectAuthorized:false` remains mandatory. In Ask, only the audited synchronous Work
mutation, pin and peer-notify owners have the stated final-boundary qualification.
Other applicable shared mutation owners refuse. Generic streaming, unmeasured
steering, arbitrary tool-internal awaits and later effect owners remain unqualified.
Controlled provider/socket/runtime fixtures are not live-provider, private-history,
whole-feature or independent final acceptance.
