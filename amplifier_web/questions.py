"""Durable, session-bound questions; preferences never grant tool permission.

The store shares the app transaction, so a question transition, its command
receipt and admission of its one answer input commit together. Runtime delivery
is a separate boundary: an uncertain send is preserved, never automatically
replayed. Native session metadata and transcripts remain owned by their stores.
"""
from __future__ import annotations

import copy
import asyncio
import json
import time
import uuid


def definitions(schema, string):
    identity = {"sessionId": {**string(200), "minLength": 1}, "id": {**string(100), "minLength": 1}}
    revision = {"expectedRevision": {"type": "integer", "minimum": 1}}
    content = {
        "prompt": {**string(8000), "minLength": 1},
        "options": {"type": "array", "maxItems": 8, "items": schema({
            "id": {**string(100), "minLength": 1}, "label": {**string(200), "minLength": 1},
            "description": string(1000)}, ["id", "label"])},
        "allowFreeText": {"type": "boolean"},
        "required": {"type": "boolean"},
        "dependency": {**string(1000), "minLength": 1},
        "artifacts": {"type": "array", "maxItems": 4, "items": schema({
            "id": {**string(100), "pattern": r"^[-\w]+$"},
            "version": {"type": "integer", "minimum": 1},
        }, ["id", "version"])},
    }
    return {
        "question.create": ("Ask a durable question in this conversation. Use Markdown and artifacts:[{id,version}] from saved Canvas receipts when review material informs the answer; versions are exact, not Latest. required=true blocks only the named dependency. This never grants permission. Keep a stable command ID for retries.", schema({"sessionId": identity["sessionId"], **content}, ["sessionId", "prompt", "required", "dependency"])),
        "question.list": ("Read this conversation's saved questions without selecting it or starting work. Pending is not answered, regardless of elapsed time.", schema({"sessionId": identity["sessionId"], "status": {"enum": ["pending", "answered", "cancelled", "superseded"]}, "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, ["sessionId"])),
        "question.read": ("Read one exact question, its answer provenance and separate runtime delivery state.", schema(identity)),
        "question.answer": ("Record an explicit answer once for this question revision. Choose optionId or text. Cite the actual user's sourceMessageId. For an answer delivered from another chat, also supply sourceRequestId from that coordination.send; the host verifies the original human input and exact handoff. Do not ask the user to repeat an answer already delivered this way. Answering does not grant tool permissions or reset budgets. Duplicate answers never replay work.", schema({**identity, **revision, "optionId": string(100), "text": string(8000), "sourceMessageId": string(200), "sourceRequestId": string(200)}, ["sessionId", "id", "expectedRevision"])),
        "question.cancel": ("Cancel a pending question at the exact revision. Cancellation is not an answer and does not authorize the dependent work.", schema({**identity, **revision, "reason": string(1000)}, ["sessionId", "id", "expectedRevision"])),
        "question.supersede": ("Replace a pending question atomically. Late answers to the old question are rejected. The replacement has its own ID and revision.", schema({**identity, **revision, **content}, ["sessionId", "id", "expectedRevision", "prompt", "required", "dependency"])),
    }


class QuestionStore:
    """JSON records in app-owned SQLite; callers own locking and commits."""

    def __init__(self, db):
        self.db = db
        self.version = 0
        db.execute("CREATE TABLE IF NOT EXISTS questions (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, created_at REAL NOT NULL, value TEXT NOT NULL)")
        db.execute("CREATE INDEX IF NOT EXISTS questions_session ON questions(session_id, created_at)")

    def put(self, record):
        self.db.execute("INSERT OR REPLACE INTO questions VALUES (?,?,?,?)", (
            record["id"], record["sessionId"], record["createdAt"], json.dumps(record)))
        self.version += 1

    def get(self, session_id, identity):
        from .errors import AppError
        row = self.db.execute("SELECT value FROM questions WHERE id=? AND session_id=?", (identity, session_id)).fetchone()
        if row is None:
            raise AppError("This question does not belong to that conversation.", 404)
        return json.loads(row[0])

    def all(self, session_id=None):
        rows = self.db.execute("SELECT value FROM questions" + (" WHERE session_id=?" if session_id else "") + " ORDER BY created_at, id", (session_id,) if session_id else ())
        return [json.loads(row[0]) for row in rows]


class Questions:
    def __init__(self, app):
        self.app = app
        self.store = QuestionStore(app.db)
        records = self.store.all()
        uncertain = {(r["sessionId"], r["delivery"].get("inputId")) for r in records
                     if r.get("status") == "answered" and
                     (r.get("delivery") or {}).get("status") in {"sending", "unknown"} and
                     r["delivery"].get("inputId") == "question:" + r["id"] + ":answer"}
        # Recovery uses only already resident evidence, once, not a transcript
        # scan per question. Cold/native-only evidence uses conversation.delivery.
        accepted = {(s["id"], m.get("inputId")) for s in app.state["sessions"]
                    for m in s.get("messages", [])
                    if (s["id"], m.get("inputId")) in uncertain and
                    m.get("role") == "user" and
                    (m.get("delivery") or {}).get("status") == "accepted"}
        for record in records:
            if (record["sessionId"], (record.get("delivery") or {}).get("inputId")) in accepted:
                record["delivery"].update(status="accepted", updatedAt=time.time())
                record["delivery"].pop("message", None)
                self.store.put(record)
            if (record.get("delivery") or {}).get("status") == "sending":
                record["delivery"].update(status="unknown", message="The app restarted before delivery was confirmed. The answer was saved; work was not replayed.", updatedAt=time.time())
                self.store.put(record)
        self._projection_version = -1
        self._grouped = {}
        self.sync()

    def for_input(self, session_id, input_id):
        """Find one immutable answer by its exact session/input binding."""
        from .errors import AppError
        if not isinstance(input_id, str) or not input_id.startswith("question:") or not input_id.endswith(":answer"):
            return None
        identity = input_id[len("question:"):-len(":answer")]
        try:
            record = self.store.get(session_id, identity)
        except AppError as exc:
            if exc.status == 404:
                return None
            raise
        if (record["status"] != "answered" or not record.get("answer") or
                (record.get("delivery") or {}).get("inputId") != input_id):
            return None
        return record

    def accept_delivery(self, session_id, input_id):
        """Apply exact positive input evidence; admission is not work completion.

        The immutable answer's saved input binding is the authority, not a
        caller's command ID, matching text, a later turn or session status.
        Callers own the service lock and publication transaction.
        """
        record = self.for_input(session_id, input_id)
        if record is None or record["delivery"]["status"] == "accepted":
            return False
        delivery = record["delivery"]
        delivery.update(status="accepted", updatedAt=time.time())
        delivery.pop("message", None)
        self.store.put(record)
        self.sync({session_id})
        return True

    def sync(self, session_ids=None):
        if session_ids is not None:
            index = self.app.projections.sessions(self.app.state)
            sessions = [index.by_id[sid] for sid in session_ids if sid in index.by_id]
            grouped = {sid: self.store.all(sid) for sid in session_ids}
        else:
            sessions = self.app.state['sessions']
            grouped = None
        if grouped is None and self._projection_version != self.store.version:
            self._grouped = {}
            for record in self.store.all():
                self._grouped.setdefault(record["sessionId"], []).append(record)
            self._projection_version = self.store.version
        if grouped is None: grouped = self._grouped
        for session in sessions:
            records = grouped.get(session["id"])
            if not records:
                # Most indexed histories have no app questions. Preserve an
                # existing empty list instead of allocating sets/generators
                # and a new projection on every streamed publication.
                if not isinstance(session.get("questions"), list) or session["questions"]:
                    session["questions"] = []
                continue
            # Retain every active/uncertain question and bounded recent history.
            visible = {r["id"] for r in records[-10:]}
            visible.update(r["id"] for r in records if r["status"] == "pending" or (r.get("delivery") or {}).get("status") in {"sending", "unknown", "rejected"})
            # An older closed question can still host a mounted review. Do not
            # evict its editor merely because newer questions filled history.
            visible.update(binding['questionId']
                           for client in self.app.clients.records.values()
                           for binding in client.get('canvasViews', {}).get('inline', {}).values()
                           if binding.get('sessionId') == session['id'] and binding.get('questionId'))
            session["questions"] = [r for r in records if r["id"] in visible]

    def read(self, action, args):
        self.app._session(args["sessionId"])
        if action == "question.read":
            return self.store.get(args["sessionId"], args["id"])
        records = self.store.all(args["sessionId"])
        records = [r for r in records if not args.get("status") or r["status"] == args["status"]]
        offset, limit = args.get("offset", 0), args.get("limit", 50)
        end = min(offset + limit, len(records))
        return {"items": records[offset:end], "total": len(records), "nextOffset": end if end < len(records) else None}

    def answer_for_dependency(self, session_id, question_id):
        """Guard for an operation explicitly bound to this question.

        The caller decides which operation depends on a question. A cancelled
        or superseded question never releases that dependency by itself.
        """
        from .errors import AppError
        record = self.store.get(session_id, question_id)
        if record["status"] != "answered":
            raise AppError("The dependent work needs an explicit answer to this question.", 409, code="question_unanswered")
        return copy.deepcopy(record["answer"])

    def _new(self, args, origin, client_id):
        from .errors import AppError
        prompt, dependency = args["prompt"], args["dependency"]
        options = copy.deepcopy(args.get("options", []))
        if not prompt.strip() or not dependency.strip():
            raise AppError("Provide the question and the work depending on its answer.")
        if any(not row["id"].strip() or not row["label"].strip() for row in options) or len({row["id"] for row in options}) != len(options):
            raise AppError("Question options need unique nonempty IDs and labels.")
        allow_text = args.get("allowFreeText", True)
        if not options and not allow_text:
            raise AppError("A question needs options or free text.")
        now = time.time()
        return {"id": str(uuid.uuid4()), "sessionId": args["sessionId"], "revision": 1,
                "status": "pending", "createdAt": now, "updatedAt": now, "prompt": prompt,
                "options": options, "allowFreeText": allow_text, "required": args["required"],
                "dependency": dependency, "createdBy": {"origin": origin, "clientId": client_id},
                "artifacts": self._artifacts(args["sessionId"], args.get("artifacts", [])),
                "contentRevision": 1, "answer": None, "delivery": None}

    def _artifacts(self, session_id, references):
        """Freeze bounded metadata, never copy a body or infer access from a URL."""
        from .canvas_versions import definition
        from .errors import AppError
        result, seen = [], set()
        for ref in references:
            key = (ref["id"], ref["version"])
            if key in seen:
                raise AppError("Use each exact question artifact only once.")
            seen.add(key)
            row = next((r for r in self.app.state.get("canvasArtifacts", [])
                        if r["id"] == ref["id"] and r.get("sessionId") == session_id), None)
            if row is None:
                raise AppError("This question artifact is unavailable in the calling conversation.", 404)
            saved = definition(row, ref["version"], self.app.db)
            digest = saved.get("body", {}).get("$resource")
            if not digest:
                raise AppError("This question artifact has no retained saved body.", 409)
            result.append({**ref, "title": saved["title"], "kind": saved["kind"],
                           "sessionId": session_id, "bodyDigest": digest})
        return result

    def review_artifact(self, session_id, question_id, question_revision, artifact_id, version):
        """Validate the immutable question binding on every preview resource read."""
        from .errors import AppError
        record = self.store.get(session_id, question_id)
        if record.get("contentRevision", 1) != question_revision:
            raise AppError("This question revision does not match the saved review.", 409)
        ref = next((r for r in record.get("artifacts", [])
                    if r["id"] == artifact_id and r["version"] == version), None)
        if ref is None:
            raise AppError("This artifact version is not attached to the question.", 409)
        current = self._artifacts(session_id, [{"id": artifact_id, "version": version}])[0]
        if current != ref:
            raise AppError("This question's saved artifact changed; no replacement was displayed.", 409)
        return ref

    def _answer_source(self, session, args, record):
        """An existing delivered handoff can carry the user's original answer.

        This records a decision, not a new permission grant. The receiving chat
        still owns its question. Do not infer human authorship from peer prose,
        and do not use an unrelated message merely because the agent can read it.
        """
        from .collaboration import PROTOCOL
        from .peer_scope import participant_matches
        from .errors import AppError

        source_session, request = session, None
        if args.get('sourceRequestId'):
            if not args.get('sourceMessageId'):
                raise AppError('Cite the original human sourceMessageId with this handoff.', 409)
            request = self.app.collaboration.receipt(args['sourceRequestId'])
            if (request.get('commandAction') != 'coordination.send'
                    or request.get('protocol') != PROTOCOL
                    or request.get('target', {}).get('sessionId') != session['id']
                    or request.get('senderSessionId') == session['id']
                    or request.get('delivery') not in {'accepted', 'applied', 'notified'}
                    or request.get('cancellation')
                    or session.get('status') in {'stopping', 'interrupted', 'stopped', 'error', 'cancelled', 'failed'}):
                raise AppError('Use an exact delivered handoff to this conversation for the answer.', 409)
            source_session = self.app._session(request['senderSessionId'], hydrate=False)
            if (not participant_matches(request, source_session)
                    or not participant_matches(request, session)
                    or request.get('sourceInterruptionRevision') != source_session.get('interruptionRevision', 0)
                    or request.get('interruptionRevision') != session.get('interruptionRevision', 0)):
                raise AppError('This answer handoff changed location or was interrupted. Read the current request.', 409)

        source = next((m for m in source_session['messages']
                       if m['id'] == args.get('sourceMessageId') and m.get('role') == 'user'), None)
        if source is None:
            raise AppError('Answer provenance must reference an original user message in this conversation or its delivered handoff.', 409)
        if source.get('inputOrigin') not in {'ui', 'user', 'voice'}:
            raise AppError('Answer provenance needs a verified user input; agent-authored or unattributed messages cannot answer for the user.', 409)
        if source.get('questionId'):
            raise AppError("Use the user's original response, not a generated question-answer receipt.", 409)
        if source.get('createdAt', 0) < record['createdAt']:
            raise AppError('The user response must follow this question.', 409)
        if request and (not source.get('inputId')
                        or source['inputId'] not in request.get('sourceInputIds', [])
                        or source.get('createdAt', 0) > request.get('acceptedAt', 0)):
            raise AppError('The handoff must retain this exact human response in its admitted input lineage.', 409)
        return source, source_session['id']

    def dispatch(self, action, args, origin, client_id, pending):
        from .errors import AppError
        app = self.app
        session = app._session(args["sessionId"])
        if action == "question.create":
            if sum(r["status"] == "pending" for r in self.store.all(session["id"])) >= 32:
                raise AppError("Resolve or cancel an existing question before adding more.", 409)
            record = self._new(args, origin, client_id)
            self.store.put(record)
            return copy.deepcopy(record)
        record = self.store.get(session["id"], args["id"])
        if record["revision"] != args["expectedRevision"] or record["status"] != "pending":
            raise AppError("This question changed or is already closed. Read it before continuing; the answer was not sent.", 409)
        # The action lock orders this check with renderer dirty declarations.
        # Refuse before changing the question, admitting an answer or replacing it.
        app.canvas_views.guard_transition(action, args)
        replacement = None
        if action == "question.supersede":
            replacement = self._new(args, origin, client_id)
            replacement["supersedes"] = record["id"]
            record.update(status="superseded", supersededBy=replacement["id"])
        elif action == "question.cancel":
            record.update(status="cancelled", cancellation={"reason": args.get("reason", ""), "origin": origin, "clientId": client_id})
        elif action == "question.answer":
            if not app.runtime:
                raise AppError("The Amplifier runtime is unavailable. The question is still pending.", 409)
            has_option, has_text = "optionId" in args, "text" in args
            if has_option == has_text:
                raise AppError("Choose one option or enter one free-text answer.")
            option = next((r for r in record["options"] if r["id"] == args.get("optionId")), None)
            text = option["label"] if option else args.get("text", "")
            if (has_option and option is None) or (has_text and (not record["allowFreeText"] or not text.strip())):
                raise AppError("Use an offered option or an allowed nonempty free-text answer.")
            source = None
            source_session_id = session['id']
            if args.get("sourceMessageId") or args.get('sourceRequestId'):
                source, source_session_id = self._answer_source(session, args, record)
            if origin == "agent" and source is None:
                raise AppError("An agent must cite the user's answer with sourceMessageId; it cannot answer on the user's behalf.", 409)
            provenance = {"origin": origin, "clientId": client_id, "via": "call" if source and source.get("via") == "call" else "chat" if source else "ui"}
            if source:
                provenance.update(messageId=source["id"], text=source.get("text", ""), inputOrigin=source['inputOrigin'], voiceId=source.get("voiceId"), voiceItemId=source.get("voiceItemId"))
                if args.get('sourceRequestId'):
                    provenance.update(sourceSessionId=source_session_id, sourceRequestId=args['sourceRequestId'])
            input_id = "question:" + record["id"] + ":answer"
            record.update(status="answered", answer={"text": text, "optionId": option["id"] if option else None,
                "questionRevision": record["revision"], "answeredAt": time.time(), "provenance": provenance,
                "artifacts": copy.deepcopy(record.get("artifacts", []))},
                delivery={"status": "sending", "inputId": input_id, "updatedAt": time.time()})
            # IDs/revisions/provenance live in metadata; the conversation keeps
            # a readable user answer, using the same content delivered to runtime.
            prompt = f'Answer to “{record["prompt"]}”:\n{text}\n\nFor: {record["dependency"]}'
            session["historyManaged"] = False
            from .chat_navigation import recent_activity
            previous_activity = recent_activity(session)
            app._message(session, "user", prompt, "question", inputId=input_id, questionId=record["id"],
                         answerProvenance=provenance, delivery={"status": "sending"})
            app._activity(session, "queued", "Your answer is queued for Amplifier.", reset=session["status"] not in {"working", "starting"})
            session["status"] = "working"
            from .execution import ensure_turn
            ensure_turn(session, input_id, prompt)
            pending.append((self.deliver, (copy.deepcopy(session), prompt, input_id, previous_activity, record["id"])))
        record.update(revision=record["revision"] + 1, updatedAt=time.time())
        self.store.put(record)
        if replacement:
            self.store.put(replacement)
            return copy.deepcopy(replacement)
        return copy.deepcopy(record)

    async def deliver(self, session, text, input_id, previous_activity, question_id):
        from .errors import AppError
        status, message = "accepted", None
        cancelled = False
        try:
            await self.app._send(session, text, input_id, previous_activity, preserve_draft=True)
        except asyncio.CancelledError:
            status, message = "unknown", "Delivery was interrupted. The saved answer has not been sent again."
            cancelled = True
        except Exception as exc:
            status = "rejected" if isinstance(exc, AppError) and exc.code == "session_busy" else "unknown"
            message = str(exc)
        async with self.app.lock:
            record = self.store.get(session["id"], question_id)
            # A late timeout/cancellation cannot erase acceptance observed by
            # the input event path while the send RPC was still outstanding.
            if record["delivery"]["status"] != "accepted":
                if status == "accepted":
                    self.accept_delivery(session["id"], input_id)
                else:
                    record["delivery"].update(status=status, updatedAt=time.time())
                    if message:
                        record["delivery"]["message"] = message
                    self.store.put(record)
                    self.sync({session['id']})
                self.app._publish_changes(sessions={session['id']})
        if cancelled:
            raise asyncio.CancelledError
