"""Read positive delivery evidence without treating missing history as non-delivery."""
import json


def receipt_mapping(raw):
    """Read a receipt without manufacturing a replacement for malformed bytes."""
    try:
        receipt = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return receipt if isinstance(receipt, dict) else None


def receipt_delivery(raw, session_id, input_id):
    """An exact, noncontradictory producer receipt; absence is not non-delivery."""
    receipt = receipt_mapping(raw)
    if receipt is None or receipt.get('sessionId') != session_id or receipt.get('inputId') != input_id:
        return None
    # Existing send receipts predate action labels. If supplied, they must agree.
    if any(receipt[key] != 'conversation.send' for key in ('action', 'commandAction') if key in receipt):
        return None
    delivery = receipt.get('delivery')
    if not isinstance(delivery, str) or delivery not in {'accepted', 'unknown', 'failed', 'sending', 'held', 'queued_for_update'}:
        return None
    if delivery == 'accepted':
        if 'accepted' in receipt and receipt['accepted'] is not True:
            return None
        if receipt.get('error') or receipt.get('code'):
            return None
        if 'status' in receipt and (type(receipt['status']) is not int or not 200 <= receipt['status'] < 300):
            return None
    return delivery


def find_message(session, input_id):
    return next((m for m in session.get('messages', [])
                 if m.get('role') == 'user' and m.get('inputId') == input_id), None)


def contains_input(messages, input_id):
    for message in messages:
        if not isinstance(message, dict) or message.get('role') != 'user':
            continue
        metadata = message.get('metadata')
        marker = metadata.get('amplifier_input') if isinstance(metadata, dict) else None
        if isinstance(marker, dict) and marker.get('id') == input_id:
            return True
    return False


def saved_delivery(session, input_id):
    from amplifier_foundation.session.history import SessionHistoryStore
    from amplifier_foundation.session.jsonl import TranscriptIndex
    from .session_files import sessions_dir, validate_id
    identity = session.get('runtimeSessionId') or session.get('nativeIdentity') or session['id']
    validate_id(identity)
    path = sessions_dir(session['workspace']) / identity / 'transcript.jsonl'
    try:
        messages = SessionHistoryStore(path.parent).indexed_messages()
        if isinstance(messages, TranscriptIndex):
            markers = messages.project('unified-delivery-v1', _input_marker)
            if input_id in markers:
                return 'accepted'
        elif contains_input(messages, input_id):
            return 'accepted'
    except (OSError, ValueError, TypeError):
        pass
    # Absence is not proof: an old worker may have acted before checkpointing.
    return 'unknown'


def _input_marker(message, index=None):
    metadata = message.get('metadata')
    marker = metadata.get('amplifier_input') if isinstance(metadata, dict) else None
    return marker.get('id') if message.get('role') == 'user' and isinstance(marker, dict) else None
