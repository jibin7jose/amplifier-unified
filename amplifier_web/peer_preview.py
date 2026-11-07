"""Receipt-bound, disposable read-only peer previews. No execution or new history store."""
from __future__ import annotations

import copy
import json
import time

from amplifier_operations.coordination import fingerprint
from .session_identity import reference
from .transcript_window import _Invalid
from .peer_scope import location, location_label, participant_matches


class PeerPreview:
    def __init__(self, service):
        self.service = service

    @property
    def windows(self):
        return self.service.history.windows

    def _target(self, identity, parent, receipt):
        from .errors import AppError
        target = self.service.directory.resolve(identity)
        if (not target or target.get('sessionKind', 'root') != 'root'
                or not participant_matches(receipt, parent) or not participant_matches(receipt, target)
                or target.get('workspaceAvailable') is False or target.get('nativeAvailable') is False
                or target.get('_deleting')):
            raise AppError('Source chat unavailable.', 403)
        return target

    @staticmethod
    def _identity(target):
        return fingerprint([reference(target), target.get('createdAt'), target.get('workspace')])

    def _descriptor(self, target):
        title = target.get('title') or 'Untitled chat'
        title = ''.join(c for c in title if ord(c) >= 32 and ord(c) != 127)[:200]
        return {'sourceId': target['id'], 'title': title or 'Untitled chat', 'available': True,
                'location': location(target), 'workspaceLabel': location_label(location(target)),
                'archived': bool(self.service.state.get('conversationOrganization', {}).get('archived', {}).get(target['id'])),
                'status': target.get('status', 'unknown')}

    def describe(self, parent, original, receipt):
        """Called only after PeerAttribution proved the exact original binding."""
        if receipt.get('origin') == 'system':
            return None
        client = self.service.clients.record()
        if not client or client.get('kind') != 'web' or client.get('selectedSessionId') != parent['id']:
            return None
        from .errors import AppError
        try:
            parent = self.service._session(parent['id'], hydrate=False)
            target = self._target(receipt['senderSessionId'], parent, receipt)
        except (ValueError, AppError):
            return {'available': False}
        payload = {'kind': 'peer-preview', 'sourceSessionId': parent['id'],
                   'messageId': original['id'], 'requestId': receipt['requestId'],
                   'binding': receipt['displayBinding'], 'targetId': target['id'],
                   'targetIdentity': self._identity(target),
                   'parentIdentity': self._identity(parent)}
        return {**self._descriptor(target), 'proof': self.windows.reader._token(payload),
                'delivery': receipt.get('delivery', 'unknown')}

    def guard(self, proof, *, parent_id=None):
        from .errors import AppError
        client = self.service.clients.record()
        try:
            value = self.windows.reader._parse(proof)
        except (_Invalid, ValueError, TypeError):
            raise AppError('This source preview expired. Reopen the original message.', 403) from None
        if (value.get('kind') != 'peer-preview' or not client or client.get('kind') != 'web'
                or client.get('selectedSessionId') != value.get('sourceSessionId')
                or parent_id is not None and value['sourceSessionId'] != parent_id):
            raise AppError('Preview the source from its original conversation.', 403)
        parent = self.service._session(value['sourceSessionId'], hydrate=False)
        if self._identity(parent) != value.get('parentIdentity'):
            raise AppError('The original conversation changed. Reopen the source preview.', 403)
        row = self.service.db.execute('SELECT receipt FROM commands WHERE id=?', (value['requestId'],)).fetchone()
        receipt = json.loads(row[0]) if row else {}
        if (receipt.get('accepted') is not True or receipt.get('commandAction') != 'coordination.send'
                or receipt.get('target', {}).get('sessionId') != parent['id']
                or receipt.get('messageId') != value['messageId']
                or receipt.get('displayBinding') != value['binding']
                or receipt.get('senderSessionId') != value['targetId']):
            raise AppError('This source message is no longer available.', 403)
        # Reuse exact-original checks, not the transcript page's broad cache
        # fence: unrelated arrivals may invalidate pages without changing this
        # receipt-bound original. Never hydrate a cold receiver for the link.
        originals = self.windows.exact_saved_originals(parent)
        original = next((m for m in originals if m.get('id') == value['messageId']), None)
        owner = {'id': parent['id'], 'messages': originals}
        if original is None or not self.service.peer_attribution.resolve(owner, [original])[0].get('attribution', {}).get('peer'):
            raise AppError('The original source binding is unavailable. Reopen its message.', 403)
        target = self._target(value['targetId'], parent, receipt)
        if self._identity(target) != value['targetIdentity']:
            raise AppError('This source identity changed. No other chat was opened.', 403)
        return value, target

    def open(self, args, origin):
        from .errors import AppError
        if origin != 'ui':
            raise AppError('A source preview requires a browser click.', 403)
        _, target = self.guard(args['proof'], parent_id=args['sourceSessionId'])
        # Identical guard to replacing the primary Canvas. Never clear dirty state.
        self.service.canvas_views.guard_transition('canvas.show', {})
        return {'accepted': True, 'result': self._descriptor(target), 'effects': []}

    async def read(self, args):
        proof = args.get('proof')
        async with self.service.lock:
            _, target = self.guard(proof)
            identity = target['id']
            descriptor = self._descriptor(target)
        # Metadata refresh is explicit and independent of body following.
        if args.get('metadata') == 'true':
            return {'source': descriptor, 'observedAt': time.time()}
        page = await self.windows.read(identity, before=args.get('before'), limit=20)
        async with self.service.lock:
            _, current = self.guard(proof)
            if current['id'] != identity:
                raise ValueError('Preview source changed.')
        result = copy.deepcopy(page)
        fields = {'id', 'role', 'text', 'textDetail', 'createdAt', 'timestampKnown', 'inputOrigin', 'via'}
        result['items'] = [{key: value for key, value in m.items() if key in fields} |
                           ({'attribution': {'caption': m['attribution']['caption']}}
                            if m.get('attribution') else {}) for m in result['items']]
        for message in result['items']:
            # Preview bodies have no mutation actions or navigable file context.
            if message.get('textDetail'):
                message['textDetail']['peerPreview'] = proof
        return {'source': self._descriptor(current), 'capturedAt': time.time(), 'page': result}

    async def read_text(self, args):
        async with self.service.lock:
            _, target = self.guard(args['peerPreview'])
            if target['id'] != args.get('sessionId'):
                from .errors import AppError
                raise AppError('The field belongs to another source.', 403)
        result = await self.windows.read_text(target['id'], args)
        async with self.service.lock:
            self.guard(args['peerPreview'])
        return result

    def select(self, args, origin):
        from .errors import AppError
        if origin != 'ui':
            raise AppError('Open chat requires a browser click.', 403)
        _, target = self.guard(args['peerPreview']['proof'], parent_id=args['peerPreview']['sourceSessionId'])
        if target['id'] != args['id']:
            raise AppError('The requested chat is not the previewed source.', 403)
