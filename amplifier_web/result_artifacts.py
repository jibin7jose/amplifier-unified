"""Request-scoped retained versions, owned by the existing output registry."""
import copy
import json
from pathlib import Path
import re

from . import retained_files
EXACT = re.compile(r'([^@\n]+)@sha256:([a-f0-9]{64})\Z')
FILE_DIGEST = re.compile(r'[@#]sha256\b', re.IGNORECASE)
URI = re.compile(r'^[a-z][a-z0-9+.-]*:', re.IGNORECASE)
DRIVE_PATH = re.compile(r'^[a-z]:[/\\]', re.IGNORECASE)
REFERENCE_HELP = (
    'To retain a local file, use path@sha256:<64 lowercase hex digits>, for example '
    'report.txt@sha256:2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824 '
    'for the exact bytes hello (no newline). Use the actual file hash. Paths resolve inside '
    'the producing execution folder; do not use file: or #sha256= syntax. A plain path or '
    'opaque citation does not save file bytes.'
)


def fence(source, request):
    return [source['id'], source['workspace'], source.get('workingDirectory'),
            source.get('executionRevision', 0), source.get('interruptionRevision', 0),
            copy.deepcopy(source.get('collaborationGeneration')),
            request.get('responseRevision', 0), copy.deepcopy(request.get('response'))]


def prepare_files(root, references):
    """No database or live session objects cross this worker boundary."""
    items = retained_files.Batch()
    try:
        return _prepare_files(root, references, items)
    except BaseException:
        items.close()
        raise


def _prepare_files(root, references, items):
    for reference in dict.fromkeys(references):
        match = EXACT.fullmatch(reference)
        file_uri = reference.lower().startswith('file:')
        if not file_uri and URI.match(reference) and not DRIVE_PATH.match(reference):
            continue  # A URL, URN or other non-file URI is only a citation.
        if file_uri or not match or '://' in match[1]:
            if file_uri or ('://' not in reference and FILE_DIGEST.search(reference)):
                items.append({'reference': reference, 'status': 'unsupported',
                              'detail': 'Unsupported retained-file reference. ' + REFERENCE_HELP})
            continue  # Opaque references are not filesystem authority.
        path, expected = match.groups()
        item = {'reference': reference, 'sha256': expected}
        try:
            item.update(retained_files.capture(root, path, expected, items))
        except (OSError, ValueError):
            # Do not expose details about a rejected external path or content.
            item.update(status='unavailable', detail='Source unavailable, changed, outside the workspace, or insufficient disk space to retain it.')
        items.append(item)
    return items


async def prepare(collaboration, source, args, origin, identity, digest):
    async with collaboration.service.lock:
        duplicate = collaboration.duplicate(identity, digest)
        if duplicate:
            return {'duplicate': duplicate}
        if origin != 'agent':
            collaboration.error('Response declarations require the actual recipient agent generation.')
        _, request, _, _ = collaboration.validate_reply(source, args)
        captured = fence(source, request)
        root = source.get('workingDirectory') or source['workspace']
    items = await retained_files.prepared(prepare_files, root, list(args.get('references', [])))
    return {'fence': captured, 'items': items}


async def store(collaboration, source, request, response, prepared, command_id):
    """Caller owns the lock and the reply transaction. Never commits itself."""
    if prepared['fence'] != fence(source, request):
        collaboration.error('The task or response changed while retaining results; inspect and retry.', 409)
    app = collaboration.service
    artifacts = []
    available = max(0, 10000 - app.db.execute('SELECT count(*) FROM output_records').fetchone()[0])
    for item in prepared['items']:
        summary = {key: value for key, value in item.items() if key not in {'resource', 'pieces', 'bodyFormat', 'path', 'filename'}}
        if item['status'] == 'retained' and not available:
            summary.update(status='unavailable', detail='The retained output relationship limit is reached.')
        elif item['status'] == 'retained':
            body = await retained_files.adopt(app.db, item)
            record = app.outputs.store.create(source['id'], {
                'kind': 'file', 'title': item['filename'], 'filename': item['filename'],
                'sha256': item['sha256'], 'bytes': item['bytes'], 'body': body, 'bodyFormat': item['bodyFormat'],
                'versionEvidence': 'snapshot', 'origin': {'source': 'agent'},
                'collaborationResult': {'requestId': request['requestId'], 'commandId': command_id,
                    'reference': item['reference'], 'generationId': response['generationId'],
                    'executionDirectory': response['executionDirectory'],
                    'executionRevision': source.get('executionRevision', 0),
                    'workspace': source['workspace']},
            })
            summary.update(outputId=record['id'], bytes=record['bytes'])
            available -= 1
        artifacts.append(summary)
    response['artifacts'] = artifacts
    response['artifactVerification'] = ('incomplete' if any(row['status'] != 'retained' for row in artifacts)
                                        else 'verified' if artifacts else 'not_requested')
    if not artifacts and response.get('references'):
        response['artifactDetail'] = 'No file versions were saved. ' + REFERENCE_HELP
    elif response['artifactVerification'] == 'incomplete':
        response['artifactDetail'] = ('Some file versions were not saved. Inspect artifacts[].status and detail '
                                      'before finishing the turn; terminal sealing does not repair retention.')


def resolve(collaboration, sid, request_id, reference, response_revision=None, *, scope='workspace'):
    """Request authority does not grant general peer output-library access."""
    app = collaboration.service
    reader = app._session(sid, hydrate=False)
    request = collaboration.receipt(request_id)
    if request.get('commandAction') != 'coordination.send':
        collaboration.error('This artifact is outside the conversation request and workspace scope.')
    collaboration.require_request_reader(request, reader, scope=scope)
    response = request.get('response') or {}
    if response_revision is not None and response.get('responseRevision') != response_revision:
        row = app.db.execute("""SELECT receipt FROM commands
            WHERE json_extract(receipt,'$.commandAction')='coordination.reply'
            AND json_extract(receipt,'$.result.requestId')=?
            AND json_extract(receipt,'$.result.responseRevision')=? LIMIT 1""",
            (request_id, response_revision)).fetchone()
        response = json.loads(row[0]).get('result') if row else None
        if not response or response.get('recipientSessionId') != request.get('target', {}).get('sessionId'):
            collaboration.error('This exact response revision is unavailable.', 404)
    if reference not in response.get('references', []):
        collaboration.error('Choose an exact artifact reference from this workspace request.')
    item = next((row for row in response.get('artifacts', []) if row['reference'] == reference), None)
    if item is None:
        return {'reference': reference, 'status': 'unavailable', 'detail': 'This older or opaque reference has no retained version.'}, None, response
    if item['status'] != 'retained':
        return copy.deepcopy(item), None, response
    try:
        record = app.outputs.record(request['target']['sessionId'], item['outputId'])
        provenance = record.get('collaborationResult') or {}
        if (provenance.get('requestId') != request_id or provenance.get('reference') != reference
                or record.get('sha256') != item['sha256']):
            raise ValueError('Retained artifact identity mismatch.')
        return {**copy.deepcopy(item), 'filename': record['filename'],
                'provenance': copy.deepcopy(provenance)}, record, response
    except (OSError, ValueError, KeyError):
        return {'reference': reference, 'status': 'unavailable',
                'detail': 'The retained version is unavailable or failed its content hash. No mutable source was substituted.'}, None, response


def image_record(collaboration, sid, record):
    """Resolve one output through its exact retained reply, never a peer library."""
    provenance = record.get('collaborationResult') or {}
    if not all(provenance.get(key) for key in ('requestId', 'commandId', 'reference')):
        raise ValueError('This output belongs to another conversation.')
    reply = collaboration.receipt(provenance['commandId'])
    response = reply.get('result') or {}
    if (reply.get('commandAction') != 'coordination.reply'
            or response.get('requestId') != provenance['requestId']
            or not isinstance(response.get('responseRevision'), int)):
        raise ValueError('This exact retained image response is unavailable.')
    item, retained, _ = resolve(collaboration, sid, provenance['requestId'],
        provenance['reference'], response['responseRevision'])
    if (retained != record or item.get('outputId') != record['id']
            or item.get('sha256') != record.get('sha256')):
        raise ValueError('This exact retained image response is unavailable.')
    return retained


async def load(collaboration, sid, request_id, reference, response_revision=None, *, scope='workspace', preview=None):
    """Detach immutable reads; recheck request authority after worker I/O."""
    app = collaboration.service
    async with app.lock:
        proof = resolve(collaboration, sid, request_id, reference, response_revision, scope=scope)
    item, record, response = proof
    if record is None:
        return item, None, response
    try:
        data = (await app.outputs.preview_async(record, *preview) if preview is not None
                else await app.outputs.content_async(record))
        if data is None:
            raise ValueError('Retained artifact bytes unavailable.')
    except (OSError, ValueError, KeyError):
        item = {'reference': reference, 'status': 'unavailable',
                'detail': 'The retained version is unavailable or failed its content hash. No mutable source was substituted.'}
        data = None
    async with app.lock:
        current = resolve(collaboration, sid, request_id, reference, response_revision, scope=scope)
        if current != proof:
            collaboration.error('The result changed while reading its retained version; inspect and retry.', 409)
    return item, data, response


async def read(collaboration, sid, args):
    item, data, response = await load(collaboration, sid, args['requestId'], args['reference'], args.get('responseRevision'),
        scope=args.get('scope', 'workspace'), preview=(args.get('offset', 0), args.get('limit', 4000)))
    result = {**item, 'responseStatus': response.get('status'), 'qualified': response.get('qualified', False),
              'independentArtifactVerification': False}
    if data is not None:
        result['downloadUrl'] = '/api/outputs/' + item['outputId'] + '/content'
        result.update(data)
    return result


def preview(state, item, data, origin):
    from .workspace_canvas import canvas_command, EXTENSIONS, _pdf, _image, MAX_TEXT, MAX_HTML
    kind = EXTENSIONS.get(Path(item['filename']).suffix.lower(), 'text')
    if kind == 'pdf':
        canvas_command(state, 'canvas.show', {'kind': 'text', 'content': '', 'title': item['filename']}, origin)
        state['canvas'].update(kind='pdf', content=_pdf(data))
    elif kind == 'image':
        canvas_command(state, 'canvas.show', {'kind': kind, 'content': _image(data), 'title': item['filename']}, origin)
    else:
        if len(data) > (MAX_HTML if kind == 'html' else MAX_TEXT):
            raise ValueError('Retained result exceeds the preview limit. Download the exact saved bytes.')
        # canvas.show has the same text limit on inline HTML; retain the existing
        # safe renderer, and expose exact downloads for larger retained files.
        canvas_command(state, 'canvas.show', {'kind': kind, 'content': data.decode('utf-8'), 'title': item['filename']}, origin)
