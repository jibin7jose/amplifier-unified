"""Deliver completed native image receipts without asking the model to attach them."""
import hashlib
import re


def completed_receipt(result):
    if hasattr(result, 'model_dump'):
        result = result.model_dump()
    if not isinstance(result, dict) or result.get('success') is False:
        return None
    value = result.get('output', result)
    if not isinstance(value, dict) or value.get('schema') != 'amplifier.image.receipt.v1' or value.get('status') != 'completed':
        return None
    if value.get('operation') not in {'generate', 'edit'}:
        return None
    if any(not isinstance(value.get(key), str) or not 0 < len(value[key]) <= 500
           for key in ('requestId', 'requestHash', 'receiptPath')):
        return None
    sha = (value.get('artifact') or {}).get('sha256') if isinstance(value.get('artifact'), dict) else None
    if any(not isinstance(v, str) or not re.fullmatch('[a-f0-9]{64}', v) for v in (value['requestHash'], sha)):
        return None
    return {key: value[key] for key in ('requestId', 'requestHash', 'receiptPath', 'operation')} | {'sha256': sha}


async def attach_completed(app, payload):
    from .image_generation import project
    from .errors import AppError
    sid, node_id, receipt = payload.get('sessionId'), payload.get('nodeId'), payload.get('receipt')
    if not isinstance(receipt, dict) or not isinstance(node_id, str):
        return
    if (any(not isinstance(receipt.get(key), str) or not 0 < len(receipt[key]) <= 500
            for key in ('requestId', 'requestHash', 'receiptPath', 'sha256'))
            or any(not re.fullmatch('[a-f0-9]{64}', receipt[key]) for key in ('requestHash', 'sha256'))
            or receipt.get('operation') not in {'generate', 'edit'}):
        return
    async with app.lock:
        try:
            session = app._session(sid)
        except AppError:
            return
        node = next((row for row in session.get('execution', {}).get('nodes', []) if row.get('id') == node_id), None)
        if not node or node.get('label') != 'image_generate' or node.get('phase') != 'completed':
            return
        job = next((row for row in project(session, session['execution']) if row['id'] == node_id), None)
        if (not job or job['phase'] != 'completed' or receipt.get('requestId') != job['requestId']
                or receipt.get('operation') != job['operation']):
            return
        args = {'sessionId': sid, 'messageId': job['messageId'],
                'title': 'Edited image' if job['operation'] == 'edit' else 'Generated image',
                'receiptPath': receipt.get('receiptPath'), 'expectedSha256': receipt.get('sha256')}
    identity = 'image-result:' + hashlib.sha256((sid + ':' + node_id).encode()).hexdigest()
    try:
        await app.outputs.dispatch('outputs.attachImage', args, 'runtime', identity, image_identity=receipt)
        async with app.lock:
            try:
                session = app._session(sid)
            except AppError:
                return
            if session.get('imageResultErrors', {}).pop(node_id, None):
                app._publish_changes(sessions={sid})
    except AppError:
        # Failure to show an already generated image must never regenerate it,
        # fail the conversation, or publish paths/provider details to the UI.
        async with app.lock:
            try:
                session = app._session(sid)
            except AppError:
                return
            errors = session.setdefault('imageResultErrors', {})
            errors[node_id] = True
            while len(errors) > 32:
                del errors[next(iter(errors))]
            app._publish_changes(sessions={sid})
