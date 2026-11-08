"""Passive navigation over canonical history; message bodies are read on demand."""
from collections import Counter
from hashlib import sha256

from .browser_detail import MESSAGE_LIMIT, TEXT_LIMIT, compact, work_segments
from .history_read_retry import retry_indexed_read


def source(session):
    if session.get('nativeProject') and session.get('historyManaged'):
        from amplifier_foundation.session.history import SessionHistoryStore
        from amplifier_foundation.session.jsonl import TranscriptIndex
        from .automatic_history import directory, display_message
        rows = SessionHistoryStore(directory(session)).indexed_messages()
        def fact(value, index):
            row = display_message(value, index, session)
            internal = row or display_message(value, index, session, include_internal=True)
            return (row['id'] if row else None, internal['role'] if internal else None,
                    not bool(row.get('observation')) if row else False, index,
                    internal.get('nativeInputId') if internal else None)
        # Cache only descriptors, including input identity multiplicity. A
        # duplicate outside the requested page must not qualify a native row.
        facts = rows.project('unified-navigation-v4:'+session['id'], fact) if isinstance(rows, TranscriptIndex) else [fact(row,i) for i,row in enumerate(rows)]
        input_counts = Counter(row[4] for row in facts if row[4])
        visible = [row for row in facts if row[0]]
        def read(positions):
            indices = [visible[position][3] for position in positions]
            bodies = rows.read_positions(indices) if isinstance(rows, TranscriptIndex) else [rows[index] for index in indices]
            selected = [display_message(body,index,session) for index,body in zip(indices,bodies)]
            for row in selected:
                if input_counts[row.get('nativeInputId')] > 1:
                    row['nativeInputAmbiguous'] = True
            return selected
        return visible, read
    rows = session.get('messages', [])
    return [(row['id'], row.get('role'), not bool(row.get('observation')), i) for i,row in enumerate(rows)], lambda positions:[rows[i] for i in positions]


def with_window(session, messages):
    """A read-only overlay for work projection, never the retained chat window."""
    rows = {row['id']: row for row in session.get('messages', [])}
    rows.update((row['id'], row) for row in messages)
    ordered = sorted(rows.values(), key=lambda row: (
        row.get('nativeIndex', float('inf')), row.get('createdAt', 0)))
    return {**session, 'messages': ordered}


def group_source(session, window_message_id=None):
    """Resolve an explicitly opened old work segment from a bounded native page.

    Navigation and subsequent lazy detail/sync reads must see the same recorded
    observations even when the host retains only the latest transcript window.
    """
    if window_message_id is None:
        return session
    if not isinstance(window_message_id, str) or not 0 < len(window_message_id) <= 200:
        raise ValueError('Choose a valid history window.')
    facts, read = source(session)
    position = next((i for i, row in enumerate(facts) if row[0] == window_message_id), None)
    if position is None:
        raise ValueError('This history window is no longer available. Refresh the conversation.')
    start = max(0, position - 5)
    return with_window(session, read(range(start, min(len(facts), start + MESSAGE_LIMIT))))


@retry_indexed_read
def query(session, *, message_id=None, window=False, _raw_window=False):
    """Pure public queries stay neutral; the host may request bounded raw rows.

    The private option carries untruncated selected bodies across the worker
    boundary, never a database callback. The HTTP owner qualifies and compacts
    them before responding. Index and preview reads retain their lazy behavior.
    """
    facts, read = source(session)
    revision = sha256('\n'.join(row[0] for row in facts).encode()).hexdigest()
    if message_id is None:
        # Only IDs and ordinals travel here, never transcript or tool text.
        return {'revision':revision, 'turns':[{'id':row[0],'position':row[3]} for row in facts if row[1]=='user' and row[2]], 'totalMessages':len(facts)}
    position = next((i for i,row in enumerate(facts) if row[0]==message_id), None)
    if position is None:
        raise ValueError('This message is no longer available. Refresh the chat navigator.')
    if not window:
        stop = next((i for i in range(position+1,len(facts)) if facts[i][1]=='user' and facts[i][2]), len(facts))
        reply = next((i for i in range(position+1,stop) if facts[i][1]=='assistant' and facts[i][2]), None)
        bodies = read([position]+([reply] if reply is not None else []))
        return {'id':message_id,'revision':revision,'text':bodies[0].get('text','')[:180], 'reply':bodies[1].get('text','')[:280] if len(bodies)>1 else ''}
    start = max(0,position-5);end=min(len(facts),start+MESSAGE_LIMIT)
    from .message_interactions import annotate
    from .peer_attribution import derive, display_annotations
    selected = derive(session, read(range(start,end)))
    annotation_source = {'messageAnnotations': display_annotations(session)}
    messages = selected if _raw_window else [
        compact(annotate(annotation_source,row),session['id'],'messages',TEXT_LIMIT) for row in selected]
    anchors={row['id'] for row in messages}
    _,segments=work_segments(with_window(session, selected),
        select_groups=lambda rows: [row for row in rows if row.get('anchorMessageId') in anchors])
    segments=[{**row, 'windowMessageId': message_id} for row in segments if row.get('anchorMessageId') in anchors]
    turn_ids={row['turnId'] for row in segments}
    return {'revision':revision,'messages':messages,'offset':start,'sourceOffset':facts[start][3],'total':len(facts),
            'before':facts[max(0,start-(MESSAGE_LIMIT-5))][0] if start else None,'after':facts[end][0] if end<len(facts) else None,
            'userOffset':sum(row[1]=='user' and row[2] for row in facts[:start]),
            'execution':{'nodes':[],'segments':segments,'turns':[row for row in session.get('execution',{}).get('turns',[]) if row['id'] in turn_ids],'detailsDeferred':True}}
