"""Independent client presentation over shared session/runtime state.

These records are disposable UI state, not conversation history. Binding is
task-local: an awaited action cannot borrow another browser's current selection.
"""
from __future__ import annotations

import copy
from collections.abc import MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
import json
import re
import time
import uuid


LOCAL_KEYS = frozenset({"view", "selectedSessionId", "selectedWorkspaceId", "canvas", "canvasTabs", "deviceCommands"})


class ClientState(MutableMapping):
    def __init__(self, shared, local):
        self.shared, self.local = shared, local

    def __getitem__(self, key):
        return (self.local if key in LOCAL_KEYS else self.shared)[key]

    def __setitem__(self, key, value):
        (self.local if key in LOCAL_KEYS else self.shared)[key] = value

    def __delitem__(self, key):
        del (self.local if key in LOCAL_KEYS else self.shared)[key]

    def __iter__(self):
        return iter(self.shared.keys() | (self.local.keys() & LOCAL_KEYS))

    def __len__(self):
        return len(set(self))

    def copy(self):
        return dict(self)

    def __deepcopy__(self, memo):
        return copy.deepcopy(dict(self), memo)


class ClientViews:
    def __init__(self, service):
        self.service = service
        self.current = ContextVar("amplifier_client", default=None)
        self.records = {}
        self.saved = {}
        self.dirty = set()
        self.startup_selections = {}
        service.db.execute("CREATE TABLE IF NOT EXISTS client_views (id TEXT PRIMARY KEY, value TEXT NOT NULL)")
        for identity, value in service.db.execute("SELECT id,value FROM client_views"):
            self.records[identity] = json.loads(value)
            self.records[identity]["canvasTabs"] = self.records[identity].get("canvasTabs") or {}
            self.saved[identity] = value
            self.startup_selections[identity] = (
                self.records[identity].get('selectedSessionId'),
                self.records[identity].get('selectedWorkspaceId'))

    @staticmethod
    def validate(identity):
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", identity):
            from .errors import AppError
            raise AppError("Choose a valid client ID.")
        return identity

    def attach(self, identity, resume=None, kind="web", *, device=None):
        from .errors import AppError
        self.validate(identity)
        if kind not in {"web", "tui", "native", "api"}:
            raise AppError("Unsupported client kind.")
        if resume is not None:
            self.validate(resume)
        if device is not None:
            for candidate in (identity, resume):
                if candidate is None:
                    continue
                previous = self.records.get(candidate)
                if (candidate == resume and previous is None) or (previous is not None and previous.get('deviceOwner') != device):
                    raise AppError("This client belongs to another connection.", 403)
        if identity not in self.records:
            previous = self.records.get(resume)
            if previous:
                record = copy.deepcopy(previous)
                if additions := getattr(getattr(self.service, "feedback", None), "additions", None):
                    additions.capture_on_attach(resume, previous, record)
                if handoffs := getattr(self.service, "handoffs", None):
                    handoffs.capture_on_attach(resume, previous, record)
            else:
                shared = self.service._state
                record = {key: copy.deepcopy(shared.get(key)) for key in LOCAL_KEYS}
                record["view"] = record.get("view") or {}
                if kind == 'web':
                    record['view']['workSurface'] = 'home'
                    record['view']['navPinned'] = False
                    record['view'].pop('workHomeView', None)
                record["canvas"] = record.get("canvas") or {}
                record["drafts"] = {}
                record["attachments"] = {}
                # Preserve pre-client drafts during the first migration only.
                if device is None and not shared.get("clientViewsMigrated"):
                    for session in shared.get("sessions", []):
                        if session.get("draft"):
                            record["drafts"][session["id"]] = session["draft"]
                        if session.get("draftAttachments"):
                            record["attachments"][session["id"]] = copy.deepcopy(session["draftAttachments"])
                    if record["view"].get("draft"):
                        record["drafts"][record["selectedSessionId"] or ""] = record["view"]["draft"]
                    shared["clientViewsMigrated"] = True
                else:
                    record["view"]["draft"] = ""
                    record["view"]["panel"] = None
            if device is not None:
                record['deviceOwner'] = device
            record.update(kind=kind, updatedAt=time.time(), deviceCommands=[])
            # A new document may restore its editor and preferences, but not an
            # uncommitted appearance preview from the document it copied.
            record["view"]["themePreview"] = False
            # A new page mounts its own inline viewers. Copying their leases
            # would retain invisible frames and stale commands from the old tab.
            views = record.get('canvasViews', {})
            for view_id, binding in views.pop('inline', {}).items():
                views.get('preferences', {}).pop(view_id + ':' + binding['resourceId'], None)
                views.pop(view_id + 'Binding', None)
            record["canvasTabs"] = record.get("canvasTabs") or {}
            # A new page has no surviving JavaScript memory. Latest opens fresh;
            # reconnecting the same client identity above keeps its binding.
            from .canvas_versions import ordinary_preview
            current = record.get('canvas', {})
            if ordinary_preview(current):
                from .canvas_library import load, scope
                scoped = {**self.service._state, **record}
                if any(row['id'] == current.get('id') and scope(scoped, row)
                       for row in self.service._state.get('canvasArtifacts', [])):
                    load(scoped, self.service.db, current['id'],
                         open_panel=current.get('open', False), version=current.get('selectedVersion'))
                    record['canvas'] = scoped['canvas']
                    # Reload replaces executable state, not the client's choice
                    # to show this exact version inline or as a saved card.
                    if current.get('_presentationDisposed'):
                        record['canvas']['_presentationDisposed'] = True
                    views.pop('primaryBinding', None)
                    views.pop('retained', None)
                    preference = views.get('preferences', {}).get('primary:' + current['id'], {})
                    preference.update(dirty=False, renderReports={})
                    for key in ('activation', 'document', 'interaction', 'events'):
                        preference.pop(key, None)
            self.records[identity] = record
            self.dirty.add(identity)
            if resume:
                shell = self.service.shell.get("client", resume)
                if shell:
                    shell.update(preview=None, reported=None)
                    self.service.shell.put("client", identity, shell)
        # Retire the saved modal route on upgrade. Do not auto-open a new
        # Canvas document over a retained viewer or its edits; the existing
        # Overview/Chat info entry opens the inspector when requested.
        if self.records[identity].get('view', {}).get('panel') == 'context-inspector':
            self.records[identity]['view']['panel'] = None
            self.dirty.add(identity)
        self.reconcile(identity)
        self.selection_revision(identity)
        return self.records[identity]

    def reconcile(self, identity):
        from .canvas_apps import sync
        sync(self.service)
        record = self.records[identity]
        self.dirty.add(identity)
        self.service.computer_visual.reconcile(identity)
        sid = record.get("selectedSessionId")
        projections = getattr(self.service, '_projections', None)
        index = projections.values.get(('session-index',)) if projections is not None else None
        rows = self.service._state.get('sessions', [])
        position = index.positions.get(sid) if index is not None else None
        if index is not None and (len(rows) != len(index.positions) or sid is not None and
                (position is None or position >= len(rows) or rows[position] is not index.by_id.get(sid))):
            # A command can alter membership before publication invalidates its
            # saved index. Reconcile against live membership, never stale cache.
            index = None
        selected = (index.by_id.get(sid) if index is not None else
                    next((row for row in rows if row['id'] == sid), None))
        directory = getattr(self.service, 'directory', None)
        if selected is None and sid is not None and directory is not None:
            selected = directory.resolve(sid)
        shared_history = self.service._state.get('sharedHistory', {})
        phase = shared_history.get('reconciliation', {}).get('phase')
        catalog_incomplete = bool(shared_history.get('loading') or shared_history.get('error')
                                  or shared_history.get('issues') or shared_history.get('issueCount')
                                  or phase is not None and phase != 'complete')
        startup = self.startup_selections.get(identity)
        # A completed bounded discovery step is not a complete directory.
        # Absence during recovery cannot discard a saved browser selection.
        unchanged = startup == (sid, record.get('selectedWorkspaceId'))
        awaiting_catalog = bool(catalog_incomplete and startup and unchanged and selected is None)
        if awaiting_catalog:
            from .managed_deletion import removed
            awaiting_catalog = not (sid in getattr(self.service, '_forgotten_session_ids', ())
                or sid in getattr(self.service, '_deleted_session_ids', ())
                or removed(self.service.db, identity=sid)
                or record.get('selectedWorkspaceId') in self.service._state.get('hiddenNativeWorkspaces', ()))
        if not awaiting_catalog or selected is not None:
            self.startup_selections.pop(identity, None)
        if sid is not None and selected is None and not awaiting_catalog:
            record["selectedSessionId"] = None
            record["canvas"] = {}
        self.service.computer_visual.reconcile(identity)
        from .managed_chats import is_managed
        selected = selected or {}
        managed = is_managed(selected) or (sid is None and is_managed(record.get("view", {}).get("newSessionDraft", {})))
        workspace = record.get("selectedWorkspaceId")
        if managed and workspace is None:
            pass
        else:
            workspaces = self.service._state.get('workspaces', [])
            position = index.workspace_positions.get(workspace) if index is not None else None
            indexed_workspace = (index.workspaces.get(workspace) if index is not None
                                 and len(workspaces) == len(index.workspaces) and position is not None
                                 and position < len(workspaces) and workspaces[position] is index.workspaces.get(workspace)
                                 else None)
            available = indexed_workspace is not None or any(row['id'] == workspace for row in workspaces)
            if not available and not (awaiting_catalog and startup[1] == workspace):
                record["selectedWorkspaceId"] = self.service._state.get("selectedWorkspaceId")
        if record.get('selectedSessionId') is None:
            # Older clients could persist an open Canvas without a chat. Hide
            # that presentation on reconnect; never discard its saved content.
            if record.get('canvas', {}).get('open'):
                record['canvas']['open'] = False
            if record['view'].get('canvasFocused'):
                record['view']['canvasFocused'] = False
        from .canvas_library import restore_body
        restore_body(record.get("canvas", {}), self.service.db)
        # The empty key is the client's pre-conversation draft. Unlike None,
        # it keeps its identity through JSON persistence and reload.
        record["view"]["messageReply"] = copy.deepcopy(record.get("messageReplies", {}).get(record.get("selectedSessionId")))
        draft = record.get("drafts", {}).get(record.get("selectedSessionId") or "", "")
        record["view"]["draftDocument"] = copy.deepcopy(record.get("draftDocuments", {}).get(record.get("selectedSessionId") or ""))
        if record["view"].get("draft") != draft:
            record["view"]["draft"] = draft
            self.dirty.add(identity)

    @contextmanager
    def bind(self, identity):
        if identity is not None:
            self.validate(identity)
            if identity not in self.records:
                from .errors import AppError
                raise AppError("Attach this client before using the session service.", 409, code="client_not_attached")
            self.reconcile(identity)
        token = self.current.set(identity)
        try:
            yield
        finally:
            self.current.reset(token)

    def state(self, shared):
        identity = self.current.get()
        if identity is not None:
            self.dirty.add(identity)
            return ClientState(shared, self.records[identity])
        return shared

    def record(self):
        identity = self.current.get()
        if identity is not None:
            self.dirty.add(identity)
        return self.records.get(identity) if identity is not None else None

    def draft(self, session_id, text, document=...):
        record = self.record()
        if document is ...:
            previous = record.get("draftDocuments", {}).get(session_id or "")
            old_text = record.get("drafts", {}).get(session_id or "", "")
            document = None
            if previous and text:
                if not text.startswith(old_text):
                    from .errors import AppError
                    raise AppError('Quoted content was kept. Edit its commentary in the composer.', 409)
                document = copy.deepcopy(previous)
                if text != old_text:
                    document['segments'].append({'type': 'text', 'text': text[len(old_text):]})
        record.setdefault("drafts", {})[session_id or ""] = text
        documents = record.setdefault("draftDocuments", {})
        if document is None:
            documents.pop(session_id or "", None)
        else:
            documents[session_id or ""] = copy.deepcopy(document)
        if session_id == record.get("selectedSessionId"):
            record["view"]["draft"] = text
            record["view"]["draftDocument"] = copy.deepcopy(document)

    def attachments(self, session):
        record = self.record()
        if record is None:
            if session is None:
                from .errors import AppError
                raise AppError('Attach a client to add files before starting a chat.')
            return session.setdefault("draftAttachments", [])
        return record.setdefault("attachments", {}).setdefault(session["id"] if session else "", [])

    def attachment_draft_id(self):
        """Identity for the private unnamed draft, not the selected chat."""
        record = self.record()
        if record is None:
            return None
        setup = record.get('view', {}).get('newSessionDraft') or {}
        location = [setup.get('workspace', ''), setup.get('location')]
        if not record.get('attachmentDraftId') or record.get('attachmentDraftLocation') != location:
            record['attachmentDraftId'] = uuid.uuid4().hex
            record['attachmentDraftLocation'] = location
            self.dirty.add(self.current.get())
        return record['attachmentDraftId']

    def bind_attachment_draft(self, session_id):
        record = self.record()
        identity = self.attachment_draft_id()
        bindings = record.setdefault('attachmentDraftBindings', {})
        bindings[identity] = session_id
        record['attachmentDraftBindings'] = dict(list(bindings.items())[-16:])
        record['attachmentDraftId'] = uuid.uuid4().hex
        self.dirty.add(self.current.get())

    def attachment_target(self, target, expected_draft_id):
        if expected_draft_id is None:
            return target  # Public explicit-chat API retains its existing contract.
        from .errors import AppError
        record = self.record()
        if record is None or target is not None:
            raise AppError('This upload is not bound to an unnamed draft.', 409)
        binding = record.get('attachmentDraftBindings', {}).get(expected_draft_id)
        if binding is not None:
            return binding
        if expected_draft_id != self.attachment_draft_id():
            raise AppError('The original new-chat draft changed. Choose the remaining files again in the intended chat.', 409)
        return None

    def consume_attachments(self, session, attachments):
        """Settle exact uploaded IDs with positive delivery, not the later RPC."""
        consumed = {row['id'] for row in attachments}
        if not consumed:
            return
        # Uploads mint unique IDs. A resumed page can copy the same draft while
        # its send is pending; settle those copies too, but only in this chat.
        # Never use the runtime callback's ambient client or current selection.
        for identity, record in self.records.items():
            draft = record.get('attachments', {}).get(session['id'], [])
            remaining = [row for row in draft if row['id'] not in consumed]
            if len(remaining) != len(draft):
                draft[:] = remaining
                self.dirty.add(identity)
        draft = session.get('draftAttachments', [])
        draft[:] = [row for row in draft if row['id'] not in consumed]

    def project(self, snapshot):
        record = self.record()
        if record is None:
            return snapshot
        if handoffs := getattr(self.service, "handoffs", None):
            snapshot["handoffs"] = handoffs.project()
        if feedback := getattr(self.service, "feedback", None):
            snapshot["feedback"] = feedback.followups.project(snapshot.get("feedback", {}))
        snapshot.pop("canvasTabs", None)
        snapshot['canvasWorkspace'] = self.service.canvas_views.project()
        from .canvas_library import presentation
        snapshot["canvasArtifacts"] = [{**row, **copy.deepcopy(presentation(self.service.state, row))}
                                       for row in snapshot.get("canvasArtifacts", [])]
        snapshot["client"] = {"id": self.current.get(), "kind": record["kind"], "protocolVersion": 1,
                              "hostInstanceId": self.service.instance_id, "reconnect": "snapshot"}
        if record['kind'] == 'web':
            snapshot['client']['build'] = self.service.installed_build.project()
        snapshot["computerVisual"] = self.service.computer_visual.project(self.current.get())
        snapshot["draftAttachments"] = copy.deepcopy(record.get("attachments", {}).get("", []))
        snapshot["attachmentDraftId"] = self.attachment_draft_id()
        from .cold_display import detached
        snapshot["sessions"] = [detached(row) for row in snapshot.get("sessions", [])]
        for session in snapshot["sessions"]:
            session["draft"] = record.get("drafts", {}).get(session["id"], "")
            session["draftAttachments"] = copy.deepcopy(record.get("attachments", {}).get(session["id"], []))
            if record['kind'] == 'web' and session['id'] == record.get('selectedSessionId'):
                session['linkedMediaAvailable'] = self.service.outputs.store.has_linked_media(session['id'])
                linked = self.service.outputs.store.has_linked_chat_references(session['id'])
                session['linkedChatReferencesAvailable'] = linked
                if linked:
                    import json
                    session['chatReferenceRevision'] = json.dumps([
                        session.get('chatReferenceVersion', 0),
                        self.service.directory.navigation_revision,
                        self.service.directory.workspace_revision,
                        # Archive/restore commits belong to organization, not
                        # directory metadata. Include its unknown epoch too.
                        self.service.shell.resources._epochs({'conversationOrganization'})])
        return snapshot

    def remember_canvas(self, record=None):
        """Keep only presentation, never another copy of an artifact body."""
        record = record if record is not None else self.record()
        if record is None or not record.get('selectedSessionId'):
            return
        canvas = record.get('canvas', {})
        record.setdefault('chatCanvas', {})[record['selectedSessionId']] = {
            'canvas': {key: copy.deepcopy(canvas[key]) for key in
                       ('id', 'open', 'selectedVersion', 'view', '_presentationDisposed') if key in canvas},
            'view': {key: copy.deepcopy(value) for key, value in record['view'].items()
                     if key.startswith('canvas')},
        }

    def restore_canvas(self):
        from .canvas_library import empty, load, restore
        record = self.record()
        if record is None:
            restore(self.service.state, self.service.db)
            return
        saved = record.get('chatCanvas', {}).get(record.get('selectedSessionId'), {})
        for key in list(record['view']):
            if key.startswith('canvas'):
                record['view'].pop(key)
        record['view'].update(copy.deepcopy(saved.get('view', {})))
        record['view'].setdefault('canvasFocused', False)
        views = record.get('canvasViews', {})
        # Inline viewers belong to the displayed conversation, not a future
        # visit. Parent transition guards have already protected dirty edits.
        for identity, binding in views.pop('inline', {}).items():
            views.get('preferences', {}).pop(identity + ':' + binding['resourceId'], None)
            views.pop(identity + 'Binding', None)
        views.pop('retained', None)
        views.pop('primaryBinding', None)
        canvas = saved.get('canvas', {})
        # Deleted artifacts must not make the chat itself impossible to open.
        from .canvas_library import scope
        available = any(row['id'] == canvas.get('id') and scope(self.service.state, row)
                        for row in self.service._state.get('canvasArtifacts', []))
        if available:
            load(self.service.state, self.service.db, canvas['id'],
                 open_panel=bool(canvas.get('open')), version=canvas.get('selectedVersion'))
            record['canvas']['view'] = copy.deepcopy(canvas.get('view', {}))
            if canvas.get('_presentationDisposed'):
                record['canvas']['_presentationDisposed'] = True
        else:
            empty(self.service.state, open_panel=bool(canvas.get('open')) and bool(record.get('selectedSessionId')))

    def selection_revision(self, identity):
        record = self.records[identity]
        canvas = record.get('canvas') or {}
        binding = [record.get('selectedSessionId'), record.get('selectedWorkspaceId'),
                   canvas.get('id'), canvas.get('selectedVersion'), bool(canvas.get('open'))]
        if record.get('_selectionBinding') != binding:
            record['_selectionBinding'] = copy.deepcopy(binding)
            record['selectionRevision'] = record.get('selectionRevision', 0) + 1
            self.dirty.add(identity)
        return record.get('selectionRevision', 0)

    def commit(self, identity, command_id, fingerprint, receipt):
        """Commit a private view with its receipt; the caller owns rollback."""
        db = self.service.db
        if command_id:
            db.execute('INSERT INTO commands VALUES (?,?,?)', (command_id, fingerprint, json.dumps(receipt)))
        written = self.save(identity, defer_ack=True)
        db.commit()
        self.acknowledge(written)

    def save(self, identity=None, *, defer_ack=False):
        pending = set(self.dirty) if identity is None else self.dirty.intersection({identity})
        written = {}
        if pending and self.service._state.get('clientViewsMigrated'):
            # Commit the one-time draft migration with its private client rows,
            # including navigation/preference paths that don't save app globals.
            from .state_records import UPSERT
            self.service.db.execute(UPSERT, ('global', 'clientViewsMigrated',
                json.dumps({'present': True, 'value': True})))
        for identity in pending:
            self.remember_canvas(self.records[identity])
            self.selection_revision(identity)
            value = copy.deepcopy(self.records[identity])
            # The artifact store already owns large bodies. A presentation
            # record keeps only the currently selected artifact and controls.
            canvas = value.get("canvas", {})
            artifact = next((a for a in self.service._state.get("canvasArtifacts", []) if a["id"] == canvas.get("id")), None)
            if artifact and artifact.get("body"):
                from .canvas_versions import view_definition
                canvas["contentResource"] = view_definition(artifact, canvas, self.service.db)["body"]
                canvas.pop("content", None)
                canvas.pop("surface", None)
            encoded = json.dumps(value)
            if self.saved.get(identity) != encoded:
                self.service.db.execute("INSERT OR REPLACE INTO client_views VALUES (?,?)", (identity, encoded))
            written[identity] = encoded
        if not defer_ack:
            self.acknowledge(written)
        return written

    def acknowledge(self, written):
        self.saved.update(written)
        self.dirty.difference_update(written)
