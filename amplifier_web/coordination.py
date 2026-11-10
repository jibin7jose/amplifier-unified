"""Explicit task/worker controls and delivery over existing authoritative stores."""
from __future__ import annotations

import hashlib
import json
import time
import uuid

from amplifier_operations.coordination import ChangeSignal, delivery
from .owned_history import SCOPE, selected, require, metadata as scope_metadata
from .task_binding import continuation_epoch
from .peer_scope import descriptor

ACTIVE = {"working", "running", "starting", "ready", "queued", "pending", "stopping"}
ATTENTION = {"error", "failed", "interrupted", "cancelled", "stopped"}


def definitions():
    from .action_schema import schema, string
    target = {"sessionId": string(200), "workerId": string(200)}
    return {
        "coordination.review": ("Read a bounded page for reviewing saved workspace work before continuing. Does not load history, start workers, inspect files or change responsibility. The source must be the calling root; cursors stay bound to its workspace. Results are observations, never permission or a readiness certificate.", schema({"sessionId": string(200), "after": string(2048), "limit": {"type": "integer", "minimum": 1, "maximum": 20}}, ["sessionId"])),
        "coordination.list": ("Find scoped root metadata by title or public/native ID without reading transcripts, selecting or starting work. query filters before paging. Default scope is caller workspace; scope=all explicitly reads registered own-history. Archived and managed roots are included; archive may narrow them. Follow nextOffset with the same filters and directoryRevision; changed catalogs refuse stale continuation. Discovery coverage is not proof of absence. includeWorkers adds saved worker details, not transcript search.", schema({"sessionId": string(200), "scope": SCOPE, "workspace": string(4000), "query": string(500), "archive": {"enum": ["all", "active", "archived"]}, "directoryRevision": string(64), "includeWorkers": {"type": "boolean"}, "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, [])),
        "coordination.wait": ("Wait on up to eight explicit targets for a report or attention. Carry each nextCursor after consuming results; retries retain result IDs. Does not run or replay work.", schema({
            "scope": SCOPE,
            "targets": {"type": "array", "minItems": 1, "maxItems": 8, "items": schema({**target, "afterCursor": string(2048)}, ["sessionId"])},
            "waitMs": {"type": "integer", "minimum": 0, "maximum": 60000},
            "maxBytes": {"type": "integer", "minimum": 4096, "maximum": 65536},
        }, ["targets"])),
        "coordination.followup": ("Send an explicit follow-up without changing selection or drafts. Agent messages to other registered roots use attributed coordination admission, including across workspaces; worker writes stay limited to the caller's own workers.", schema({**target, "text": string(100000)}, ["sessionId", "text"])),
        "coordination.interrupt": ("Request interruption of an explicit conversation or worker. A request does not prove cancellation or undo effects.", schema(target, ["sessionId"])),
    }


def observe_worker(worker, payload):
    """Retain bounded report receipts beside their existing worker lifecycle row."""
    report = payload.get("report")
    if not isinstance(report, str) or not report:
        return
    identity = payload.get("reportId") or "legacy-" + hashlib.sha256(json.dumps(
        [payload.get("runId"), payload.get("callId"), report], separators=(",", ":")
    ).encode()).hexdigest()
    receipts = worker.setdefault("reportReceipts", [])
    if any(row["id"] == identity for row in receipts):
        return
    sequence = worker["reportSequence"] = worker.get("reportSequence", 0) + 1
    receipts.append({"id": identity, "sequence": sequence, "text": report[:20000],
        "runId": payload.get("runId"), "source": "worker", "sourceTruncated": bool(payload.get("reportTruncated") or len(report) > 20000),
        "sourceChars": payload.get("reportSourceChars", len(report)), "window": payload.get("reportWindow", "prefix"), "at": payload.get("updatedAt") or time.time()})
    del receipts[:-32]


class Coordination:
    def __init__(self, service):
        self.service = service
        self.signal = ChangeSignal()

    def notify(self):
        self.signal.notify()

    def task(self, sid):
        return self.service.state.get("runtimeControl", {}).get(sid, {}).get("task.get", {}).get("task") or {}

    def snapshot(self, target, *, include_results=True, session=None, task=None, live=True):
        session = self.service._session(target["sessionId"]) if session is None else session
        wid = target.get("workerId")
        worker = next((row for row in session.get("workers", []) if row["id"] == wid), None) if wid else None
        if wid and worker is None:
            raise ValueError("Worker not found in the specified conversation")
        value = worker if wid else session
        if wid and not live and (value.get('status') in ACTIVE
                                 or value.get('persistent') and value.get('status') == 'idle'):
            # A saved persistent worker is not a live process after restart.
            # Match admission's interrupted projection without mutating storage.
            value = {**value, 'status': 'interrupted'}
        status = value.get("status", "interrupted")
        approvals = [row["id"] for row in session.get("approvals", []) if row.get("status") == "pending" and (not wid or row.get("sessionId") in {wid, value.get("sessionId")})]
        # Saved question/task references remain owned by their existing stores.
        task = self.task(session["id"]) if task is None else task
        questions = [row["id"] for row in session.get("questions", []) if row.get("status") == "pending"]
        results = (value.get("reportReceipts", []) if wid else [
            {"id": message["id"], "sequence": index + 1, "text": message.get("text", ""),
                "inputId": message.get("inputId"), "source": message.get("source", message.get("via", "conversation"))}
            for index, message in enumerate(m for m in session.get("messages", []) if m.get("role") == "assistant")
        ]) if include_results else []
        latest = value.get("reportSequence", 0) if wid else len(results)
        # Status changes during work do not repeatedly wake waiting callers.
        signal = {"status": status if status not in ACTIVE else "active", "approvals": approvals, "questions": questions,
            "taskId": task.get("id"), "taskStatus": task.get("status"),
            "taskContinuationEpoch": continuation_epoch(task), "interruptionRevision": value.get("interruptionRevision", 0)}
        # Transition to another active phase is progress only. Starting a new
        # turn is also quiet; the next report/attention signal wakes the waiter.
        if status in ACTIVE:
            signal["status"] = "waiting"
        return {"identity": json.dumps([session["id"], wid], separators=(",", ":")),
            "target": {"sessionId": session["id"], **({"workerId": wid} if wid else {})},
            "kind": "worker" if wid else "conversation", "title": value.get("name", session.get("title", "Conversation")),
            "status": status, "parentSessionId": value.get("parentSessionId") if wid else session.get("parentSessionId"),
            "runtimeSessionId": wid if wid and value.get("kind") == "session" else session.get("runtimeSessionId"),
            "runId": value.get("runId") if wid else None, "callId": value.get("callId") if wid else None,
            "taskId": value.get("taskId") if wid else task.get("id"),
            "operationId": "worker:" + wid if wid else None,
            "attention": bool(approvals or questions or status in ATTENTION or task.get("status") == "blocked"),
            "approvalIds": approvals[:64], "questionIds": questions[:64], "taskQuestionIds": task.get("questionIds", [])[:64],
            "canFollowup": bool(self.service.runtime and not session.get("historyReadOnlyReason") and (not wid or value.get("persistent") and status in {"idle", "running"})),
            "canInterrupt": bool(self.service.runtime and (status in ACTIVE or wid and value.get("persistent") and status == "idle")),
            "results": results, "latestSequence": latest, "signal": signal,
            "wakeable": status not in ACTIVE or bool(approvals or questions)}

    def list(self, args, caller=None):
        # One pinned directory view binds indices to identity. Native discovery
        # can change independently of the app revision; never continue silently.
        reader = self.service.directory.read_snapshot(self.service._state)
        try:
            owner = (scope_metadata(self.service, caller or self.service.state.get('selectedSessionId'))
                     if caller or args.get('scope') == 'workspace' else None)
            archived = set(self.service._state.get('conversationOrganization', {}).get('archived', {}))
            binding = [reader.observation_revision['navigation'], owner, sorted(archived),
                       {key: args.get(key) for key in
                        ('scope', 'workspace', 'sessionId', 'query', 'archive', 'includeWorkers')}]
            token = hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            if args.get('directoryRevision') not in (None, token):
                raise ValueError('Saved chat directory or lookup filters changed. Restart at offset 0.')
            result = self._list(args, caller, reader, owner, archived)
            discovery = self.service._state.get('sharedHistory', {})
            complete = not bool(discovery.get('loading') or discovery.get('error') or discovery.get('issueCount')
                                or discovery.get('issues')) and discovery.get('reconciliation', {}).get('phase') in {None, 'complete'}
            return {**result, 'directoryRevision': token, 'scope': selected(args, 'workspace' if caller else 'all'),
                    'discovery': {**{key: discovery.get(key) for key in ('loading', 'error', 'issueCount', 'reconciliation')},
                                  'complete': complete, 'coverage': 'Current registered directory metadata; no transcript reads. Incomplete discovery is not proof of absence.'}}
        finally:
            reader.close()

    def _list(self, args, caller, directory, owner, archived):
        scope = selected(args, 'workspace' if caller else 'all')
        workspace = args.get('workspace')
        if owner and workspace and scope != 'all' and workspace != owner.get('workspace'):
            raise ValueError('Workspace is outside the requested history scope. Use explicit scope=all for registered own-history.')
        offset, limit = args.get("offset", 0), args.get("limit", 100)
        from .chat_navigation import _matches
        query = args.get('query', '').strip()
        archive = args.get('archive', 'all')
        def eligible(row):
            return ((archive == 'all' or (row['id'] in archived) == (archive == 'archived'))
                    and (not query or _matches([row.get(key) for key in
                         ('title', 'description', 'id', 'nativeIdentity', 'runtimeSessionId', 'workspace')], query)))
        if args.get('sessionId'):
            if owner:
                require(self.service, owner['id'], args['sessionId'], scope)
            metadata = directory.resolve(args['sessionId'])
            if metadata is None:
                from .errors import AppError
                raise AppError('Conversation not found.', 404)
            sessions = [metadata] if (not workspace or metadata.get('workspace') == workspace) and eligible(metadata) else []
            sessions = sessions[offset:offset + limit + 1]
        else:
            # Scope is part of the indexed query, before offset/limit. A
            # managed caller's missing workspace is an exact NULL scope, not
            # permission to enumerate other registered workspaces.
            predicates, params = ["session_kind='root'"], []
            if owner and scope == 'workspace':
                predicates.append('workspace IS ?')
                params.append(owner.get('workspace'))
            elif owner and scope == 'task':
                predicates.append('id=?')
                params.append(owner['id'])
            if workspace:
                predicates.append('workspace=?')
                params.append(workspace)
            if query:
                directory.db.create_function('discovery_match', 7,
                    lambda query, *fields: int(_matches(fields, query)), deterministic=True)
                predicates.append('discovery_match(?,title,description,id,native_id,alias_id,workspace)')
                params.append(query)
            if archive != 'all':
                directory.db.create_function('discovery_archived', 1, lambda identity: int(identity in archived), deterministic=True)
                predicates.append('discovery_archived(id)' if archive == 'archived' else 'NOT discovery_archived(id)')
            sessions = directory.select(' AND '.join(predicates), params,
                offset=offset, limit=limit + 1)
        more = len(sessions) > limit
        sessions = sessions[:limit]
        include_workers = args.get("includeWorkers", not bool(caller))
        residents = {row['id']: row for row in self.service.state['sessions']}
        items = []
        for session_number, metadata in enumerate(sessions):
            session = residents.get(metadata['id'])
            if session is None:
                session = metadata
                if include_workers:
                    # Explicit worker detail reads only this page's saved views;
                    # no worker is prepared and no owner enters the working set.
                    from .directory_state import _owner
                    from .state_records import load_owner
                    saved, _ = load_owner(self.service.db, session['id'])
                    if saved is None and self.service.db.execute('SELECT 1 FROM history_local_rows WHERE id=?', (session['id'],)).fetchone():
                        raise ValueError('Saved conversation details are unavailable; its directory entry was retained.')
                    session = _owner(directory, metadata, self.service.state)
            task = self.task(session['id'])
            if session['id'] not in self.service.state.get('runtimeControl', {}):
                from .state_records import load_owner
                from .cold_display import ColdRecord
                _, control = load_owner(self.service.db, session['id'])
                task = ColdRecord(control or {}, self.service.db).get('task.get', {}).get('task') or {}
            for target in [{"sessionId": session["id"]}, *({"sessionId": session["id"], "workerId": worker["id"]} for worker in session.get("workers", []) if include_workers)]:
                item = self.snapshot(target, include_results=False, session=session, task=task,
                                     live=session['id'] in residents)
                if not target.get("workerId"):
                    details = descriptor(session)
                    item.update(workspace=session.get("workspace"), collaboration=session.get("collaboration"),
                                location=details['location'], workspaceLabel=details['workspaceLabel'],
                                archived=session['id'] in archived,
                                sessionKind=metadata.get('sessionKind', 'root'),
                                parentId=metadata.get('nativeParentId') or metadata.get('parentId'))
                    from .session_identity import reference
                    item['sessionRef'] = reference(metadata)
                if caller and session["id"] != caller:
                    permitted = session.get('sessionKind', 'root') == 'root' and not target.get("workerId")
                    modes = ["auto", "notify", "queue"]
                    if self.service.collaboration.can_steer(session):
                        modes.append("steer")
                    item.update(canMessage=permitted, canFollowup=permitted, canInterrupt=False,
                                peerActions=[{"modes": modes}] if permitted else [])
                # Discovery needs the interruption revision, not the full
                # wait fingerprint's unbounded pending approval/question lists.
                item["signal"] = {"interruptionRevision": item["signal"]["interruptionRevision"]}
                items.append({key: value for key, value in item.items() if key not in {"results", "identity", "wakeable"}})
                if len(items) >= args.get("limit", 100):
                    return {"items": items, "truncated": more if not include_workers else True,
                            "nextOffset": offset + session_number + 1 if more or include_workers else None}
        return {"items": items, "truncated": more, "nextOffset": offset + limit if more else None}

    async def wait(self, args, caller=None):
        targets = args["targets"]
        identities = [(row["sessionId"], row.get("workerId")) for row in targets]
        if len(set(identities)) != len(identities):
            raise ValueError("Wait targets must be distinct")
        max_bytes = args.get("maxBytes", 32768) // len(targets)
        from .errors import AppError
        def read():
            results, errors = [], []
            for target in targets:
                try:
                    if caller:
                        require(self.service,caller,target['sessionId'],selected(args))
                    results.append(delivery(self.snapshot(target), target.get("afterCursor"), max_bytes=max_bytes))
                except (ValueError, LookupError, AppError) as exc:
                    errors.append({"target": {key: target[key] for key in ("sessionId", "workerId") if key in target}, "error": str(exc)})
            return {"targets": results, "errors": errors, "changed": bool(errors or any(row["changed"] for row in results))}
        return await self.signal.wait(read, wait_ms=args.get("waitMs", 0))

    async def dispatch(self, action, args, origin, command_id, include_state, caller_session_id, *, admission_check=None):
        from .errors import AppError
        if action == "coordination.review":
            from .workspace_review import review
            async with self.service.lock:
                return {"accepted": True, "result": review(self.service, args, origin, caller_session_id)}
        if action not in definitions():
            return await self.service.collaboration.dispatch(action, args, origin, command_id, caller_session_id,
                                                              admission_check=admission_check)
        if action in {"coordination.list", "coordination.wait"}:
            if origin not in {'ui', 'user'} and not caller_session_id:
                raise AppError('History reads require a registered calling conversation.', 403)
            if action == "coordination.list":
                async with self.service.lock:
                    return {"accepted": True, "result": self.list(args, caller_session_id)}
            await self.service._flush_pending_progress()
            reader = caller_session_id or (self.service.state.get('selectedSessionId') if 'scope' in args else None)
            if action == "coordination.wait":
                for target in args["targets"]:
                    if reader:
                        try:
                            require(self.service,reader,target['sessionId'],selected(args))
                        except (AppError, ValueError):
                            continue  # Return per-target scope errors without loading history.
                    if not target.get("workerId"):
                        await self.service.history.ensure_loaded(target["sessionId"])
            result = self.list(args, caller_session_id) if action == "coordination.list" else await self.wait(args,reader)
            return {"accepted": True, "result": result}
        # No model-supplied authorization flag can widen this host-bound scope.
        if origin not in {"ui", "user"} and (not caller_session_id or args["sessionId"] != caller_session_id):
            if action == "coordination.followup" and not args.get("workerId"):
                return await self.service.dispatch("conversation.send", args, origin, command_id,
                    include_state=include_state, caller_session_id=caller_session_id)
            raise AppError("A user must explicitly send follow-ups or interruptions to another conversation.", 403)
        command_id = command_id or str(uuid.uuid4())
        target = self.snapshot(args)
        retried = bool(command_id and self.service.db.execute("SELECT 1 FROM commands WHERE id=?", (command_id,)).fetchone())
        wid = args.get("workerId")
        if action == "coordination.followup":
            if not args["text"].strip():
                raise AppError("Enter a follow-up message.")
            if not retried and wid and not target["canFollowup"]:
                raise AppError("This worker cannot receive a follow-up. Its saved result remains available.", 409)
            underlying = "worker.message" if wid else "conversation.send"
            values = {"sessionId": args["sessionId"], "text": args["text"], **({"id": wid} if wid else {"preserveDraft": True})}
            if not wid:
                values.update({key: value for key, value in args.items() if key in {"grantId", "mode", "references", "replyToRequestId", "senderSessionId"}})
        else:
            if not retried and not target["canInterrupt"]:
                raise AppError("This target has no live work to interrupt.", 409)
            underlying = "worker.stop" if wid else "conversation.stop"
            values = {"sessionId": args["sessionId"], **({"id": wid} if wid else {})}
        result = await self.service.dispatch(underlying, values, origin, command_id, include_state=include_state, caller_session_id=caller_session_id)
        return {**result, "commandId": command_id}
