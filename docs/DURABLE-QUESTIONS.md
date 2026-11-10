# Durable questions

Unified can save a question while the agent continues independent work. Questions
belong to one conversation and survive page reconnects and service restarts.
They are choices or requests for information, never tool-permission approvals.

The UI shows options, an optional free-text field, and whether the named step
needs an answer. Optional questions can be skipped. Recent answers and closed
questions remain inspectable. A saved answer and runtime delivery have separate
states, including a visible unconfirmed-delivery state.

## Shared actions

Discover exact schemas using `app_control` with `operation: "list_actions"` and
`args: {"prefix": "question."}`. UI and agent calls use the same actions:

| Action | Contract |
| --- | --- |
| `question.create` | Save prompt, options/free text, explicit `required`, and `dependency` describing the waiting work. Returns immediately. |
| `question.list` | Read a bounded page with optional status filter; follow `nextOffset`. |
| `question.read` | Inspect one question, answer provenance and delivery state. |
| `question.answer` | Submit either `optionId` or `text`, bound to `sessionId`, question `id`, and exact `expectedRevision`. |
| `question.cancel` | Close a pending question without an answer. |
| `question.supersede` | Atomically close the old question and create a linked replacement. Late answers to the old question fail. |

Agent calls are bound to the calling conversation; `sessionId` is supplied by
the bridge if omitted. Agents conveying a text or voice answer must cite
`sourceMessageId` from an actual user message recorded after the question. The
answer can be in that conversation or carried by an existing delivered
`coordination.send`: also provide its `sourceRequestId`, and the host derives the
source chat from the retained receipt. The exact human input must belong to that
send's admitted source lineage and precede the handoff. The receipt must target
the question owner; both participant locations and interruption revisions must
still match. Queued, uncertain, cancelled and foreign handoffs cannot supply an
answer, and this route cannot resume a stopped or failed recipient. Managed chats
and other workspaces in the same installation use the same path.

The service saves source text, the original chat and handoff identities, and,
for call transcripts, voice and item IDs. The question still belongs to its
recipient; no peer may mutate an unrelated question, manufacture a human answer,
change permissions or reset a task budget. This records a decision already made
by the user instead of requiring a duplicate click in another chat. A generated
answer receipt cannot serve as user provenance.
Agent-authored or unattributed messages cannot be used as human answers, even
when they appear with the user role. New UI and voice inputs carry host-owned
origin metadata through edits. Older unattributed inputs require a fresh explicit
answer through the question card or a new recorded user response. UI submissions
retain the originating client identity. Permission-request state
is unaffected by these actions.

Use one stable command ID to retry the same mutation. Retrying an accepted answer
returns its current saved record and never sends another runtime input. Reusing
the command ID with changed contents fails. New commands against answered,
cancelled, superseded or stale revisions also fail.

## Persistence and delivery

`questions.py` owns `QuestionStore`, backed by an injected SQLite connection. Its
table is `questions(id, session_id, created_at, value)` with a session index. The
app owns locking and commits. Each JSON record includes:

- `id`, `sessionId`, `revision`, `status`, `createdAt`, and `updatedAt`;
- `prompt`, `options`, `allowFreeText`, `required`, and `dependency`;
- `createdBy`, and optional cancellation or replacement links;
- `answer`, including the answered question revision, timestamp and provenance;
- `delivery`, with a stable `question:<id>:answer` input ID, timestamp and status.

Question transitions and the command receipt commit in the app transaction before
runtime admission. Runtime delivery uses the existing conversation input path,
preserving drafts and selection. `accepted` means the runtime admitted the input,
not that dependent work completed. Duplicate submissions produce one saved answer
and one delivery attempt. Cancellation or a hard exit during delivery preserves
an `unknown` outcome; restart does not repeat uncertain work. A definite ownership
rejection is reported separately as `rejected`.

Exact positive input evidence also reconciles the saved question, including a
late send acknowledgement or processed-input event after a timeout. Acceptance
cannot be overwritten by a later timeout or cancellation. Restart repairs a
stale question from an already retained accepted message without loading dormant
history or starting a runtime. The saved answer, question revision, provenance
and exact artifact references do not change.

Current question projections appear in `session.questions` for the browser and
agent state. All pending and unconfirmed records plus recent history are shown;
older records remain available through the question read/list actions. Questions
are stored in the app database, not in native session metadata or presentation
files. Forks therefore do not inherit live questions or answer them for the source.

## Dependency and recovery boundaries

A required question blocks its **named dependency**, while independent work can
continue. No timeout, cancellation, skip or superseding action counts as an
answer. Agent guidance enforces this distinction for model-driven work. Host
operations that explicitly depend on a question can call
`Questions.answer_for_dependency(session_id, question_id)`; it rejects every
unanswered state, including cancelled and superseded questions. The service does
not infer dependencies among arbitrary tool calls or globally pause a session.

When delivery is unknown, inspect the saved answer and current work before any
deliberate continuation. **Check delivery** uses the existing
`conversation.delivery` action for that answer's exact input ID. It reads positive
runtime/native-history evidence without resending or warming a worker. Missing
or failed evidence leaves the answer visibly unconfirmed, including after
reconnect; independent work can continue. An accepted input is not proof that
the answer was processed or that dependent work completed.
There is no automatic retry/replay or cross-host delivery
reconciliation. Cross-host question transfer and operation-level dependency
binding are integration work for the owning portability/continuation services.

## Validation

`tests/test_questions.py` covers restart and hard process exit, stable receipts,
wrong-session/stale/superseded answers, input validation, provenance, permission
separation, independent work and draft preservation.
`tests/test_question_handoffs.py` adds real service/transport-bound handoffs,
original human provenance, cross-workspace/managed locations, changed Stop and
location boundaries, supersession and exactly-once answer admission. The runtime
is scripted; these checks do not claim live-model cooperation.

After [preparing the client and host test tools](../components/README.md) and building, run
`node tests/web/questions-browser.mjs`. It exercises the actual UI, service,
SQLite persistence and shared action path with an isolated deterministic runtime.
It covers reconnects, options/free text, cancellation, duplicate rejection, voice
transcript provenance, preserved drafts and mobile layout. It does not establish
model behavior, microphone quality, or live provider acceptance.
