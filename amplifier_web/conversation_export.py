"""Read-only, complete public conversation snapshots, independent of UI paging."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re

from amplifier_foundation.session.history import SessionHistoryStore

from .automatic_history import directory, display_message
from .session_files import sessions_dir
from .session_store import _index_visible


def _saved_messages(home, session):
    identity = session.get('nativeIdentity') or session.get('runtimeSessionId') or session['id']
    root = (directory(session) if session.get('nativeProject') else
            sessions_dir(session['workspace']) / identity if session.get('workspace') else None)
    if root is not None and any((root / name).exists() for name in ('transcript.jsonl', 'transcript.jsonl.backup')):
        history = SessionHistoryStore(root, session_id=identity).load(include_events=False)
        if any(item.code == 'changed_during_read' for item in history.diagnostics):
            raise ValueError('The conversation changed during export. Please retry.')
        # A recovered backup is useful for browsing but cannot be described as
        # a complete current export. Do not silently export an older snapshot.
        if any(item.source.startswith('transcript') for item in history.diagnostics):
            raise ValueError('The saved transcript needs recovery before a complete export is available.')
        return history.messages
    legacy = Path(home) / 'sessions' / identity / 'checkpoint.json'
    if legacy.is_file():
        from .host.storage import SessionStore
        return SessionStore(legacy.parent.parent).load(identity)[0]
    if session.get('nativeProject') or session.get('sharedHistoryTotal', 0):
        raise ValueError('The saved transcript is unavailable. Restore it before exporting the complete conversation.')
    return []  # New, imported, and voice-only conversations can be UI-owned.


def _public_reference(row):
    """Decode labelled host history without exporting its provider instructions."""
    text = row.get('text', '')
    if row.get('_voiceReference') is not None:
        return row['_voiceReference']
    if row.get('_visibleReference'):
        try:
            values = json.loads(text.split('\n', 1)[1])
            if not isinstance(values, list):
                raise ValueError('Invalid reference')
            return [item for item in values if isinstance(item, dict)
                    and item.get('role') in {'user', 'assistant'} and isinstance(item.get('text'), str)]
        except (ValueError, IndexError, TypeError):
            raise ValueError('A saved conversation reference could not be read safely.') from None
    if not row.get('nativeInputId') and row.get('role') == 'user' and text.startswith('This is a user message arriving through the voice interface of this same Amplifier conversation. '):
        try:
            reference, current = text.split('\n</voice_reference>\nCurrent spoken user request:\n', 1)
            values = json.loads(reference.split('<voice_reference>\n', 1)[1])
            if not isinstance(values, list):
                return None
            rows = [{**item, 'via': 'call'} for item in values if isinstance(item, dict)
                    and item.get('role') in {'user', 'assistant'} and isinstance(item.get('text'), str)]
            if not any((item['role'], item['text']) == ('user', current) for item in rows):
                rows.append({'role': 'user', 'text': current, 'via': 'call'})
            return rows
        except (ValueError, IndexError, TypeError):
            return None  # Ordinary user text is never erased by a partial match.
    return None


def _export_message(row, index, session):
    # A visible-reference wrapper is hidden in chat, but its contained public
    # exchanges still belong in a complete export. Only the strict decoder
    # below may expose them; never render the host instruction text itself.
    reference = bool((row.get('metadata') or {}).get('amplifier_visible_reference'))
    displayed = display_message(row, index, session, include_internal=reference)
    if displayed is not None and reference:
        displayed['_visibleReference'] = True
    if displayed is not None and displayed.get('voiceDelegation'):
        from .voice_input_projection import public_input
        from .session_store import text_content
        voice = public_input(text_content(row), (row.get('metadata') or {}).get('amplifier_input'))
        values = [{'role': item['role'], 'text': item['text'], 'via': 'call'} for item in voice['history']]
        if not any((item['role'], item['text']) == ('user', voice['text']) for item in values):
            values.append({'role': 'user', 'text': voice['text'], 'via': 'call'})
        current = next(index for index in range(len(values) - 1, -1, -1)
                       if (values[index]['role'], values[index]['text']) == ('user', voice['text']))
        values[current] = {**displayed, '_canonicalVoiceRequest': True}
        displayed['_voiceReference'] = values
    return displayed


def messages(home, session):
    saved = _saved_messages(home, session)
    visible = copy.deepcopy(session.get('messages', []))
    native = []
    for index, row in enumerate(saved):
        displayed = _export_message(row, index, session)
        if displayed is not None:
            native.append(displayed)
    from collections import Counter
    input_counts = Counter(row.get('nativeInputId') for row in native)
    for row in native:
        if row.get('nativeInputId') and input_counts[row['nativeInputId']] > 1:
            row['nativeInputAmbiguous'] = True
    from .voice_input_projection import align as align_voice_inputs
    visible, voice_aliases = align_voice_inputs({**session, 'messages': visible}, native)
    from .automatic_history import alias_input_originals, align_expanded_inputs
    visible = align_expanded_inputs(visible, voice_aliases, peer_only=True)
    aliases = alias_input_originals({**session, 'messages': visible}, voice_aliases, peer_only=True)
    by_id = {row['id']: row for row in aliases if row.get('nativeMessageId')}
    visible = [by_id.get(row['id'], row) for row in visible]
    # Validate explicit anchors before mixing two different versions of history.
    for row in visible:
        index = row.get('nativeIndex')
        if type(index) is int:
            if not 0 <= index < len(saved):
                raise ValueError('The saved conversation was rewritten. Refresh it before exporting.')
            canonical = _export_message(saved[index], index, session)
            if canonical is None:
                if (saved[index].get('metadata') or {}).get('ephemeral'):
                    continue  # Omit old UI copies of now-hidden ephemeral rows.
                raise ValueError('The saved conversation was rewritten. Refresh it before exporting.')
            from .session_store import matches_user
            bound_peer = (row['id'] in by_id and row.get('nativeMessageId') == canonical['id']
                          and row.get('inputId') == canonical.get('nativeInputId'))
            if (canonical['role'], canonical['text']) != (row.get('role'), row.get('text')) and not matches_user(saved[index], row) and not bound_peer:
                raise ValueError('The saved conversation was rewritten. Refresh it before exporting.')
    visible = [row for row in visible if type(row.get('nativeIndex')) is not int
               or (0 <= row['nativeIndex'] < len(saved) and _export_message(saved[row['nativeIndex']], row['nativeIndex'], session) is not None)]
    # Main-session replies to a voice delegation are canonical chat messages;
    # only recorded voice items are separate, UI-owned spoken exchanges.
    calls = [row for row in visible if row.get('via') == 'call' and not row.get('voiceId')]
    for row in calls:
        row['via'] = 'chat'
    _index_visible(saved, visible, session.get('sharedHistoryOffset', 0))
    for row in calls:
        row['via'] = 'call'
    positions = {row['nativeIndex']: index for index, row in enumerate(native)}
    combined, cursor = [], 0
    for row in visible:
        position = positions.get(row.get('nativeIndex'))
        if position is not None:
            combined.extend(native[cursor:position])
            cursor = max(cursor, position + 1)
            if native[position].get('_visibleReference'):
                row['_visibleReference'] = True
            if native[position].get('_voiceReference') is not None and not row.get('_voiceReference'):
                combined.append(native[position])
        combined.append(row)
    combined.extend(native[cursor:])
    # Legacy reference records contain ordered role/text/via values, but no
    # stable message IDs or timestamps. Match *occurrences*, never a text set:
    # two separate "yes" turns remain two turns. Reference-only recovery stays
    # at its saved native anchor and is labelled as having uncertain placement.
    public_ui = [row for row in visible if not row.get('_visibleReference') and _public_reference(row) is None]
    reference_claims = set()
    voice_window = []
    voice_ui_cursor = 0
    result = []
    for row in combined:
        if row.get('role') not in {'user', 'assistant'} or row.get('ephemeral') or row.get('thinking') or (row.get('metadata') or {}).get('ephemeral'):
            continue
        reference = _public_reference(row)
        if reference is not None:
            if not row.get('_visibleReference'):
                # Consecutive voice delegations carry overlapping recent-speech
                # windows. Remove only a contiguous suffix/prefix overlap;
                # never collapse repeated occurrences inside a window.
                keys = [(item['role'], item['text']) for item in reference]
                overlap = next((size for size in range(min(len(voice_window), len(keys)), 0, -1)
                                if voice_window[-size:] == keys[:size]), 0)
                voice_window.extend(keys[overlap:])
                reference = [item for index, item in enumerate(reference)
                             if index >= overlap or item.get('_canonicalVoiceRequest')]
            ui_cursor = 0 if row.get('_visibleReference') else voice_ui_cursor
            for item in reference:
                match = next((index for index in range(ui_cursor, len(public_ui))
                              if (not row.get('_visibleReference') or index not in reference_claims)
                              and (not item.get('_canonicalVoiceRequest') or public_ui[index].get('nativeMessageId') == item['id'])
                              and (public_ui[index].get('role'), public_ui[index].get('text')) == (item['role'], item['text'])
                              and (not item.get('via') or public_ui[index].get('via') == item['via'])), None)
                if match is not None:
                    ui_cursor = match + 1
                    if row.get('_visibleReference'):
                        reference_claims.add(match)
                else:
                    result.append({**item, '_recoveredReference': not item.get('_canonicalVoiceRequest', False)})
            if not row.get('_visibleReference'):
                voice_ui_cursor = ui_cursor
        else:
            result.append(row)
    return result


def _label(value):
    return re.sub(r'[\r\n]+', ' ', str(value)).replace('`', '\\`')


def chat_reference_lines(references, message, *, include_ids=False):
    """Inert labels in source position; no target contents or executable URLs."""
    if message.get('role') != 'assistant' or message.get('observation'):
        return []
    import html
    def plain(value):
        text = html.escape(re.sub(r'[\r\n]+', ' ', str(value)), quote=False)
        return re.sub(r'([\\`*_{}\[\]()#+.!~:|>-])', r'\\\1', text)
    result = []
    for item in references:
        if message.get('id') not in item['messageIds']:
            continue
        # Export/copy may read after asynchronous qualification. Match the
        # frozen row's exact bytes/index as well, never merely its old UUID.
        import hashlib
        expected = item['messageHashes'].get(message.get('id'))
        if expected is not None:
            if hashlib.sha256(message.get('text', '').encode()).hexdigest() != expected:
                continue
        elif item['canonicalMessageId'] and type(message.get('nativeIndex')) is int:
            from .automatic_history import display_identity
            if display_identity(item['sourceScope'], message['nativeIndex'], 'assistant',
                                message.get('text', '')) != item['canonicalMessageId']:
                continue
        else:
            continue
        text = 'Chat reference: ' + plain(item['title']) + ' — ' + plain(item['workspace'])
        text += ' (host-local reference; ' + ('unavailable' if not item['available'] else 'open in Unified') + ')'
        if include_ids:
            text += ' — ID: ' + plain(item['targetSessionId'])
        result.append(text)
    return result


def _timestamp(value):
    from datetime import datetime, timezone
    if type(value) not in (int, float):
        return None
    try:
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _execution_summaries(session, selected_ids, scope):
    """Only structural facts; labels, summaries and payloads may contain secrets."""
    from collections import Counter
    tree = session.get('execution', {})
    result = {}
    phases = {'completed', 'failed', 'cancelled', 'interrupted', 'running', 'working',
              'starting', 'queued', 'pending', 'retrying', 'outcome_unknown', 'stopped'}
    kinds = {'tool': 'tool actions', 'llm': 'model calls', 'worker': 'workers'}
    for turn in tree.get('turns', []):
        anchor = turn.get('anchorMessageId')
        if scope != 'all' and anchor not in selected_ids:
            continue
        counts = Counter(kinds[node['kind']] for node in tree.get('nodes', [])
                         if node.get('turnId') == turn.get('id') and node.get('kind') in kinds)
        phase = turn.get('phase') if turn.get('phase') in phases else 'unknown'
        text = 'Work status: ' + phase.replace('_', ' ')
        if counts:
            text += ' · ' + ', '.join(f'{count} {kind}' for kind, count in sorted(counts.items()))
        start, end = turn.get('startedAt'), turn.get('endedAt')
        if type(start) in (int, float) and type(end) in (int, float) and 0 <= end-start < 31536000:
            text += f' · {end-start:.1f} seconds'
        result.setdefault(anchor if anchor in selected_ids else None, []).append(text + '.')
    return result


def snapshot(home, session, artifacts, options=None, *, resolver=None, message_rows=None, chat_references=()):
    options = options or {}
    rows = messages(home, session) if message_rows is None else message_rows
    scope = options.get('scope', 'all')
    if scope not in {'all', 'from', 'range'}:
        raise ValueError('Choose a full conversation, a starting point, or a message range.')
    first, last = options.get('fromMessageId'), options.get('throughMessageId')
    if scope == 'all' and (first or last) or scope == 'from' and last:
        raise ValueError('The message boundaries do not match the chosen export scope.')
    if scope != 'all':
        def boundary(identity):
            matches = [index for index, row in enumerate(rows) if identity and row.get('id') == identity]
            if len(matches) != 1:
                raise ValueError('An export boundary is missing or ambiguous. Refresh and select the messages again.')
            return matches[0]
        start = boundary(first)
        end = boundary(last) if scope == 'range' else len(rows) - 1
        if end < start:
            raise ValueError('The end of the export must follow its starting message.')
        rows = rows[start:end + 1]
    from .peer_attribution import derive
    rows = derive(session, rows, resolver)
    minimal = options.get('minimal', False)
    selected_ids = {row.get('id') for row in rows if row.get('id')}
    reference_ids = options.get('includeReferenceIds', True)
    execution = _execution_summaries(session, selected_ids, scope) if options.get('includeExecution') else {}
    owned = [row for row in artifacts if row.get('sessionId') == session['id']]
    omitted_artifacts = 0
    if scope != 'all':
        included = [row for row in owned if row.get('messageId') in selected_ids]
        omitted_artifacts = len(owned) - len(included)
        owned = included
    blocks = ['# ' + _label(session.get('title') or 'Conversation'),
              'Conversation: `' + _label(session['id']) + '`',
              'Snapshot of user-visible conversation text. Tool payloads and hidden instructions are omitted.']
    if not options.get('includeConversationId', not minimal):
        blocks = [blocks[0], blocks[2]]
    if scope != 'all':
        blocks.append('Selected conversation excerpt; earlier and later messages outside this range are omitted.')
    if session.get('status') in {'working', 'starting', 'running', 'stopping'}:
        blocks.append('Work was in progress when this snapshot was captured; the final response may follow later.')
    if any(row.get('_recoveredReference') for row in rows):
        blocks.append('Recovered reference entries retain their saved reference position. Their original conversation positions are unavailable.')
    for row in rows:
        role = 'User' if row['role'] == 'user' else 'Assistant'
        if row.get('observation'):
            role = 'Service observation'
        if row.get('via') == 'call' or row.get('voiceId'):
            role += ' (voice)'
        if row.get('_recoveredReference'):
            role += ' — recovered reference'
        blocks.append('## ' + role)
        if row.get('attribution'):
            blocks.append(row['attribution']['caption'])
        # Deliberately no strip/normalization: Markdown fences, indentation and
        # trailing spaces in visible source are part of the exported message.
        if options.get('includeTimestamps') and (stamp := _timestamp(row.get('createdAt'))):
            blocks.append('Recorded: ' + stamp)
        blocks.append(row.get('text', ''))
        blocks.extend(chat_reference_lines(chat_references, row, include_ids=reference_ids))
        blocks.extend(execution.get(row.get('id'), []))
        for item in row.get('attachments', []):
            if isinstance(item, dict):
                blocks.append('Attachment: ' + _label(item.get('name', 'File')) + (' (ID: `' + _label(item.get('id', 'unavailable')) + '`)' if reference_ids else ''))
    if owned:
        blocks.append('## Artifacts\n\nReferences identify saved artifacts in this host; their contents are not embedded.')
        for row in owned:
            blocks.append('- ' + _label(row.get('title') or row.get('kind') or 'Artifact')
                          + ((' — ID: `' + _label(row['id']) + '`'
                          + ('; message: `' + _label(row['messageId']) + '`' if row.get('messageId') else '')) if reference_ids else ''))
    if execution.get(None):
        blocks.append('## Other retained work\n\nThese work records have no matching message in this snapshot.')
        blocks.extend(execution[None])
    if options.get('includeExecution'):
        blocks.append('Execution summaries cover retained work records only; they are not a complete tool audit.')
    if not rows:
        blocks.append('No messages yet.')
    content = '\n\n'.join(blocks) + '\n'
    summary = {'scope': scope, 'minimal': bool(minimal), 'messageCount': len(rows),
               'executionSummaryCount': sum(map(len, execution.values())),
               'bytes': len(content.encode('utf-8')), 'attachmentCount': sum(
                   sum(isinstance(item, dict) for item in row.get('attachments', [])) for row in rows),
               'artifactCount': len(owned), 'omittedArtifactCount': omitted_artifacts,
               'fileBytesIncluded': False,
               'fromMessageId': rows[0].get('id') if rows else None,
               'throughMessageId': rows[-1].get('id') if rows else None,
               'omissions': ['Hidden instructions, private reasoning and tool payloads are excluded.',
                             'Attachment and artifact contents are not embedded.']}
    if omitted_artifacts:
        summary['omissions'].append('Artifacts without a message link inside the selected range are excluded.')
    return content, summary


def markdown(home, session, artifacts):
    return snapshot(home, session, artifacts)[0]
