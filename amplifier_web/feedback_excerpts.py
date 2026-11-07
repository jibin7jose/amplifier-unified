"""Explicit, locally reviewed text snapshots; never implicit feedback context."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import time

MAX_BYTES = 64_000


def definitions(schema, string):
    return {
        'feedback.excerpt.review': (
            'Prepare an editable local feedback excerpt from a minimal Markdown session.export snapshot. Supply the exact reviewed edited text on a later call to freeze edits. Returns exact text, redactions/warnings, hash and verified GitHub repository visibility. This read-only GitHub check never publishes. Outside Full autonomous work, user review and disclosure consent are required before staging. Preparation is not independent or human verification.',
            schema({'id': string(200), 'snapshotId': string(200), 'text': string(MAX_BYTES)}, ['id', 'snapshotId'])),
        'feedback.excerpt.stage': (
            'Stage the exact prepared same-conversation excerpt for its frozen GitHub destination. A current Full agent, assigned native child or trusted system with an explicit calling conversation needs no browser, acknowledgeDisclosure or acknowledgeWarnings; selection retains actual agent/system authority. Other callers require disclosure/warning acknowledgement and the selected connected browser for agents; feedback.submit then requires exact user-confirmed excerpt hashes. Preparation never implies human review. Nothing uploads yet. Exact requestId retries never add twice.',
            schema({'id': string(200), 'clientId': string(200), 'reviewId': string(64),
                    'requestId': {'type':'string','pattern':'^[A-Za-z0-9_-]{8,100}$'},
                    'acknowledgeDisclosure': {'const': True}, 'acknowledgeWarnings': {'type': 'boolean'}},
                   ['id','reviewId','requestId'])),
    }


def redact(text, paths=()):
    """Best effort only: preserve all unredacted text, never truncate silently."""
    counts = {}
    def replace(pattern, label, flags=0):
        nonlocal text
        text, count = re.subn(pattern, '[' + label + ' REDACTED]', text, flags=flags)
        if count:
            counts[label] = counts.get(label, 0) + count
    # Known environment credentials; values never enter a report or log.
    from .diagnostics import SECRET_KEY
    for name, value in os.environ.items():
        if SECRET_KEY.search(name) and len(value) >= 8:
            replace(re.escape(value), 'KNOWN SECRET')
    replace(r'-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----', 'PRIVATE KEY', re.S)
    replace(r'(?i)\bBearer\s+[^\s\"\'<>]+', 'TOKEN')
    replace(r'(?i)\b(?:api[_-]?key|password|secret|access[_-]?token|refresh[_-]?token)\b[\"\']?\s*[:=]\s*[\"\']?[^\s,;\"\'<>]+', 'CREDENTIAL')
    replace(r'\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,})', 'TOKEN')
    replace(r'https?://[^\s<>\"`)]+', 'URL')
    for path in sorted({str(Path.home()), *paths} - {'', '/'}, key=len, reverse=True):
        replace(re.escape(path) + r'[^\s\"\'<>`)]*', 'PATH')
    replace(r'(?<![\w:/])(?:[A-Za-z]:\\|\\\\|/)[^\s\"\'<>`)]+', 'PATH')
    replace(r'(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b', 'EMAIL')
    replace(r'https?://[^\s<>\"`)]+', 'URL')
    warnings = ['Review every line. Detection is incomplete; names, private business information and other sensitive text may remain.']
    if re.search(r'\b(?:\d[ -]?){9,16}\b', text):
        warnings.append('Possible phone, account or other identifying number remains; remove it unless essential.')
    return text, [{'kind': key, 'count': value} for key, value in counts.items()], warnings


class Excerpts:
    def __init__(self, feedback):
        self.feedback, self.service = feedback, feedback.service
        self.service.db.execute('CREATE TABLE IF NOT EXISTS feedback_excerpts (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, payload TEXT NOT NULL)')

    async def review(self, args):
        from . import feedback
        from .state_storage import resource
        async with self.service.lock:
            exported = copy.deepcopy(self.service.state.get('conversationExports', {}).get(args['snapshotId']))
            if not exported or exported['sessionId'] != args['id']:
                raise ValueError('Choose an export from this conversation.')
            if not exported['summary'].get('minimal'):
                raise ValueError('Create a minimal-context Markdown export before preparing feedback.')
            text = args.get('text', resource(self.service.db, exported['content']['$resource']))
            source = self.service._session(args['id'])
            paths = [str(self.service.data_dir), source.get('workspace') or '']
        if not text.strip() or len(text.encode('utf-8')) > MAX_BYTES:
            raise ValueError('Choose a smaller excerpt, up to 64 KB. Nothing was truncated or sent.')
        # Avoid publishing a title or attachment/reference identifiers by default.
        if 'text' not in args:
            text = '# Conversation excerpt\n' + text.split('\n', 1)[-1]
            if exported['summary'].get('attachmentCount'):
                text = re.sub(r'^Attachment: .* \(ID: `[^\n]*`\)$', '[Attachment reference omitted; no file content included.]', text, flags=re.M)
            if exported['summary'].get('artifactCount'):
                # Only the generated trailing footer is metadata. An ordinary
                # message can contain an Artifacts heading without losing text.
                footer = '\n\n## Artifacts\n\nReferences identify saved artifacts in this host; their contents are not embedded.'
                text = text.rsplit(footer, 1)[0]
        text, redactions, warnings = redact(text, paths)
        if len(text.encode('utf-8')) > MAX_BYTES:
            raise ValueError('Choose a smaller excerpt, up to 64 KB after redaction. Nothing was truncated or sent.')
        target = await feedback.github_api('repos/' + feedback.REPOSITORY, None)
        if type(target.get('private')) is not bool or target.get('full_name', '').casefold() != feedback.REPOSITORY.casefold():
            raise ValueError('The feedback destination visibility could not be verified. Nothing was sent.')
        visibility = 'private' if target['private'] else 'public'
        sha = hashlib.sha256(text.encode()).hexdigest()
        summary = exported['summary']
        heightened = summary['scope'] == 'all' or summary['messageCount'] > 20 or len(text.encode()) > 16000 or bool(redactions) or len(warnings) > 1
        value = {'sessionId': args['id'], 'snapshotId': args['snapshotId'], 'text': text, 'sha256': sha,
                 'filename': 'conversation-excerpt-' + sha[:12] + '.md', 'bytes': len(text.encode()),
                 'repository': feedback.REPOSITORY, 'visibility': visibility, 'redactions': redactions,
                 'warnings': warnings, 'requiresExtraReview': heightened, 'summary': summary}
        if type(target.get('id')) is int:
            value['repositoryId'] = target['id']
        identity = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        value.update(reviewId=identity, checkedAt=time.time())
        async with self.service.lock:
            self.service.db.execute('INSERT OR IGNORE INTO feedback_excerpts VALUES (?,?,?)',
                                    (identity, args['id'], json.dumps(value)))
            self.service.db.commit()
        return value

    def stage(self, args, origin=None, caller=None):
        from .errors import AppError
        from . import feedback_context
        authority = feedback_context.capture(self.service, origin, caller)
        row = self.service.db.execute('SELECT session_id,payload FROM feedback_excerpts WHERE id=?', (args['reviewId'],)).fetchone()
        if not row or row[0] != args['id']:
            raise AppError('This reviewed excerpt belongs to a different conversation or is unavailable.', 409)
        if authority and authority['sessionId'] != args['id']:
            raise AppError('Full feedback excerpts must belong to the calling conversation.', 409)
        if not authority and self.service.state.get('selectedSessionId') != args['id']:
            raise AppError('The selected conversation changed. Open the reviewed conversation before attaching its excerpt.', 409)
        review = json.loads(row[1])
        if not authority and (args.get('acknowledgeDisclosure') is not True or review['requiresExtraReview'] and args.get('acknowledgeWarnings') is not True):
            raise AppError('Review the excerpt and acknowledge its disclosure and warnings before attaching it.')
        previous = self.service.db.execute('SELECT metadata FROM feedback_attachments WHERE request_id=?', (args['requestId'],)).fetchone()
        if previous and json.loads(previous[0]).get('excerpt', {}).get('reviewId') != args['reviewId']:
            raise AppError('This staging request ID belongs to a different review.', 409)
        payload = {'requestId': args['requestId'], 'name': review['filename'],
                   'base64': base64.b64encode(review['text'].encode()).decode()}
        self.feedback.attachment_command('feedback.attachment.add', payload, origin, caller, authority=authority)
        stored = self.service.db.execute('SELECT metadata FROM feedback_attachments WHERE request_id=?', (args['requestId'],)).fetchone()
        item = json.loads(stored[0])
        if item.get('_sessionId') != (authority['sessionId'] if authority else None):
            raise AppError('This staging request belongs to a different feedback owner.', 409)
        item['excerpt'] = {key: review[key] for key in ('reviewId','sha256','repository','visibility','bytes')}
        if 'repositoryId' in review:
            item['excerpt']['repositoryId'] = review['repositoryId']
        self.service.db.execute('UPDATE feedback_attachments SET metadata=? WHERE request_id=?', (json.dumps(item), args['requestId']))
        if not authority:
            for selected in self.service.state['view']['feedbackDraft']['attachments']:
                if selected['id'] == item['id']:
                    selected['excerpt'] = item['excerpt']
        return feedback_context.public_file(item)
