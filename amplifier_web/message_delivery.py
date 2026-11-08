"""Read positive delivery evidence without treating missing history as non-delivery."""


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
