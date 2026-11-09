"""Deduplicated public execution tree and usage rollups (no reasoning payloads)."""
import math
import time
from .token_usage import with_gross_tokens

LIVE_PHASES={'running','working','starting','queued','pending','retrying','idle'}

def voice_usage_node(node):
    return node.get('lifecycle') == 'voice' or (node.get('kind') == 'llm'
        and node.get('provider') == 'OpenAI voice' and str(node.get('id', '')).startswith('voice:'))


def voice_usage_turns(tree):
    identities = {turn['id'] for turn in tree.get('turns', []) if turn.get('lifecycle') == 'voice'}
    # Older call-only turns predate explicit lifecycle ownership. Realtime
    # uses a response ID where Live uses "session"; manager input turns have
    # a different exact identity and must retain their work lifecycle.
    identities.update(node['turnId'] for node in tree.get('nodes', []) if voice_usage_node(node)
        and node.get('turnId') and str(node.get('id', '')).rsplit(':', 1)[0] == node['turnId'])
    return identities


def pending_voice_node(node):
    return voice_usage_node(node) and node.get('phase') in LIVE_PHASES and not node.get('endedAt')


def pending_voice_usage(tree):
    return any(pending_voice_node(node)
               for node in [*tree.get('nodes', []), *tree.get('retiredUsageNodes', [])]) or any(
        turn.get('lifecycle') == 'voice' and turn.get('phase') in LIVE_PHASES
        for turn in tree.get('turns', []))


def restore_voice_usage(session):
    """Only host restart/admission loses the provider observer, never manager idle."""
    reference = dict.get(session, '_coldFields', {}).get('execution', {})
    # A completed manager turn does not rule out a later Realtime response.
    # Legacy summaries need one exact-owner read; new metadata avoids it.
    if not dict.__contains__(session, 'execution') and reference.get('pendingVoice') is False:
        return False
    tree = session.get('execution', {})
    turns = voice_usage_turns(tree)
    changed = False
    ended = time.time()
    for node in [*tree.get('nodes', []), *tree.get('retiredUsageNodes', [])]:
        if not voice_usage_node(node) or node.get('phase') not in LIVE_PHASES or node.get('endedAt'):
            continue
        node.update(lifecycle='voice', phase='incomplete', endedAt=ended)
        if str(node.get('id', '')).endswith(':session'):
            node['usage'] = {**node.get('usage', {}), 'usageFinalized': False, 'usageStatus': 'incomplete'}
        changed = True
    for turn in tree.get('turns', []):
        if turn['id'] in turns and turn.get('phase') in LIVE_PHASES:
            turn.update(lifecycle='voice', phase='incomplete', endedAt=ended)
            changed = True
    if changed:
        refresh_usage(tree)
    return changed


def interrupt_owned_workers(session, runtime_owner):
    """Retain unknown outcomes after the exact native process has exited.

    An independent chat, unbound historical row or replacement process is not
    owned by this receipt. Terminal results and reports remain authoritative.
    """
    if not runtime_owner:
        return
    for worker in session.get('workers', []):
        if (worker.get('runtimeOwnerId') == runtime_owner and
                (worker.get('status') in {'starting', 'running', 'working', 'queued', 'pending', 'stopping'}
                 or worker.get('persistent') and worker.get('status') == 'idle')):
            worker.update(status='interrupted', updatedAt=time.time())

def interrupt_unfinished(session):
    """Settle lost execution without interpreting a later warm worker as success."""
    reference = dict.get(session, '_coldFields', {}).get('execution', {})
    if (not dict.__contains__(session, 'execution')
            and (reference.get('pendingWork') is False or
                 'pendingWork' not in reference and reference.get('pendingObservation') is False
                 and session.get('status') not in {'working', 'starting', 'ready', 'stopping'})
            and session.get('status') != 'working'):
        return False
    tree = session.get('execution', {})
    independent = voice_usage_turns(tree)
    live = any(turn.get('phase') in LIVE_PHASES and turn['id'] not in independent
               for turn in tree.get('turns', []))
    if not live and session.get('status') != 'working':
        return False
    finish(session, 'interrupted')
    session['status'] = 'interrupted'
    if not session.get('error'):
        summary = 'Work was interrupted before its outcome could be confirmed.'
        session['error'] = summary
        session['errorAt'] = time.time()
        session['failure'] = {
            'category': 'execution_interrupted', 'summary': summary,
            'guidance': 'Some actions may have finished. Review saved results before continuing; no work was replayed.',
            'errorType': 'ExecutionInterrupted',
            'recordedAt': session['errorAt'],
        }
    return True

USAGE_KEYS=('inputTokens','outputTokens','cacheReadTokens','cacheWriteTokens','totalTokens','grossInputTokens','grossTotalTokens','reasoningTokens')

def ensure_turn(session,identity,label=''):
    tree=session.setdefault('execution',{'nodes':[],'turns':[],'currentTurnId':None})
    if identity and not any(t['id']==identity for t in tree['turns']):
        from .duration_hints import shape, estimate
        request_shape = shape(session, identity, label)
        hint = estimate(tree['turns'], request_shape)
        started = time.time()
        tree['turns'].append({'id':identity,'inputId':identity,'label':label[:100],'startedAt':started,'phase':'running','aggregateUsage':rollup([]),
                             **({'durationShape': request_shape} if request_shape else {})})
        session['turnDurationHint'] = {**hint, 'inputId': identity, 'startedAt': started} if hint else None
    anchor_turns(session)
    if identity:tree['currentTurnId']=identity
    return tree


def anchor_turns(session):
    """Pin work where it began, including voice work without a visible input ID.

    A null anchor means before the first message. Never move a saved anchor when
    a later transcript, response, completion, or worker update arrives.
    """
    messages=[m for m in session.get('messages',[]) if m.get('id')]
    for turn in session.get('execution',{}).get('turns',[]):
        if 'anchorMessageId' in turn:
            continue
        exact=next((m for m in messages if m.get('role')=='user' and (
            m['id'] in {turn.get('messageId'),turn.get('userMessageId')} or
            any(m.get(key) and m[key] in {turn.get('inputId'),turn.get('id')}
                for key in ('inputId','voiceInputId')))),None)
        started=turn.get('startedAt')
        preceding=[m for m in messages if isinstance(started,(int,float)) and
                   isinstance(m.get('createdAt'),(int,float)) and m['createdAt']<=started]
        anchor=exact or (preceding[-1] if preceding else None)
        turn['anchorMessageId']=anchor['id'] if anchor else None


def rollup(calls):
    result={key:0 for key in USAGE_KEYS}
    result['metricKnownCalls']={key:0 for key in USAGE_KEYS}
    result.update(calls=len(calls),costUsd=0.0,pricedCalls=0,estimatedCalls=0,unknownCalls=0,tokenUnknownCalls=0,tokenPendingCalls=0,costPendingCalls=0)
    voice_calls=[node for node in calls if any(key in (node.get('usage') or {}) for key in ('voiceSeconds','usageFinalized'))]
    if voice_calls:
        result.update(voiceSessionCalls=len(voice_calls),voiceKnownCalls=0,voicePendingCalls=0,voiceIncompleteCalls=0)
    for node in calls:
        usage=with_gross_tokens(node.get('usage') or {})
        for key in USAGE_KEYS:
            value=usage.get(key)
            if isinstance(value,(int,float)) and value>=0:
                result[key]+=value
                result['metricKnownCalls'][key]+=1
        pending=node.get('phase',node.get('status')) in LIVE_PHASES and not node.get('endedAt')
        if type(usage.get('voiceSeconds')) in (int,float) and math.isfinite(usage['voiceSeconds']) and usage['voiceSeconds'] >= 0:
            result['voiceSeconds']=result.get('voiceSeconds',0)+usage['voiceSeconds']
            result['voiceKnownCalls']+=1
        if 'voiceSeconds' in usage or 'usageFinalized' in usage:
            result['voicePendingCalls']+=int(pending)
            result['voiceIncompleteCalls']+=int(not pending and usage.get('usageFinalized') is not True)
        if not usage or not any(k in usage for k in ('inputTokens','outputTokens','totalTokens')):
            result['tokenUnknownCalls']+=1
            result['tokenPendingCalls']+=int(pending)
        cost=usage.get('costUsd')
        if isinstance(cost,(int,float)) and cost>=0:
            result['costUsd']+=cost;result['pricedCalls']+=1
            result['estimatedCalls']+=int(usage.get('costType')=='estimated')
        else:
            result['unknownCalls']+=1
            result['costPendingCalls']+=int(pending)
    result['costType']='unavailable' if not result['pricedCalls'] else 'partial' if result['unknownCalls'] else 'estimated' if result['estimatedCalls'] else 'reported'
    return result


def ingest(session,event):
    tree=ensure_turn(session,None)
    identity=event.get('id')
    if not identity:return
    allowed={'id','revision','producerId','budgetRevision','admittedAt','parentId','turnId','sessionId','rootSessionId','kind','phase','label','toolCallId','provider','model','routing','runId','parentProvider','startedAt','endedAt','usage','summary','input','output','error','lifecycle','failure','liveObservation'}
    safe={k:v for k,v in event.items() if k in allowed}
    from .tool_observations import metadata as tool_metadata
    safe.update(tool_metadata(event))
    for key in ('assignmentId', 'assignmentRevision', 'assignmentReservation'):
        if key in event:
            safe[key] = event[key]
    if event.get('kind') == 'tool' and event.get('label') in {'image_generate', 'nano-banana'} and event.get('imageGeneration'):
        from .image_generation import metadata
        image = metadata(event['imageGeneration'])
        if image:
            safe['imageGeneration'] = image
    if 'routing' in safe:
        from .host.model_selection import public_routing
        safe['routing'] = public_routing(safe['routing'])
    if safe.get('kind') != 'tool':
        for key in ('input','output','error'):safe.pop(key,None)
    node=next((n for n in tree['nodes'] if n['id']==identity),None)
    prior_topology = tuple(node.get(key) for key in ('kind', 'parentId', 'turnId')) if node else None
    if node:
        if safe.get('revision', 0) < node.get('revision', 0): return
        if node.get('phase') == 'outcome_unknown' and safe.get('phase') in LIVE_PHASES and safe.get('revision', 0) <= node.get('revision', 0): return
        if node.get('kind') == 'llm' and node.get('endedAt') and not safe.get('endedAt') and safe.get('revision', 0) <= node.get('revision', 0): return
        if safe.get('kind') == 'worker' and safe.get('runId') and safe['runId'] != node.get('runId'):
            for field in ('provider', 'model', 'parentProvider', 'routing'):
                node.pop(field, None)
        if (safe.get('kind') == 'tool' and safe.get('toolObservationId')
                and safe['toolObservationId'] != node.get('toolObservationId')):
            # A new exact observation cannot inherit another event's details.
            for field in ('input', 'output', 'error', 'inputDetail', 'outputDetail',
                          'errorDetail', '_eventFields', 'inputSummary', 'outcomeSummary', 'inputPreview', 'actionPath', 'tool'):
                node.pop(field, None)
        if event.get('liveObservation') and event.get('phase') == 'running':
            for field in ('input', 'output', 'error', 'inputDetail', 'outputDetail', 'errorDetail', '_eventFields', 'imageGeneration'):
                node.pop(field, None)
        node.update(safe)
    else:
        node=safe;tree['nodes'].append(node)
    # Tool output/progress cannot change model-call usage. Preserve the existing
    # aggregates when neither call membership nor ownership/turn mapping changed.
    # A late parent or an unassigned turn still requires the complete rebuild.
    if node.get('kind') in {'tool', 'worker'} and tree.get('aggregateUsage') is not None:
        topology = tuple(node.get(key) for key in ('kind', 'parentId', 'turnId'))
        if (prior_topology == topology and node.get('aggregateUsage') is not None
                and all(row.get('turnId') for row in tree['nodes'])):
            return
        if (prior_topology is None and node.get('turnId')
                and all(row.get('turnId') and row.get('parentId') != identity
                        for row in tree['nodes'])):
            node['aggregateUsage'] = rollup([])
            return
    refresh_usage(tree)


def refresh_usage(tree):
    lookup={n['id']:n for n in tree['nodes']}
    for candidate in tree['nodes']:
        if not candidate.get('turnId'):
            parent=lookup.get(candidate.get('parentId'),{})
            candidate['turnId']=parent.get('turnId') or tree.get('currentTurnId')
    # Usage is counted once per LLM call. Tool/worker summaries never add their
    # own accumulated totals, preventing double counting at every ancestor.
    calls=[n for n in tree['nodes'] if n.get('kind')=='llm']
    descendants={n['id']:[] for n in tree['nodes']}
    by_turn={}
    for call in calls:
        by_turn.setdefault(call.get('turnId'),[]).append(call)
        cursor=call;seen=set()
        while cursor and cursor['id'] not in seen:
            seen.add(cursor['id']);descendants[cursor['id']].append(call)
            cursor=lookup.get(cursor.get('parentId'))
    for candidate in tree['nodes']:candidate['aggregateUsage']=rollup(descendants[candidate['id']])
    # A live event must not rescan every historical call for every old turn.
    for turn in tree['turns']:turn['aggregateUsage']=rollup(by_turn.get(turn['id'],[]))
    tree['aggregateUsage']=rollup(calls)


def generation_turns(session):
    """Project exact root outcomes over older idle/completed receipts."""
    outcomes, generations = {}, {}
    events = session.get('generations', [])
    starts = [event for event in events if event.get('event') == 'generation.started'
              and event.get('sessionId') == session.get('id') and event.get('rootSessionId') == session.get('id')]
    started_generations = {event.get('generation_id') for event in starts if event.get('generation_id')}
    started_inputs = {event.get('initial_input_id') for event in starts if isinstance(event.get('initial_input_id'), str)}
    for order, event in enumerate(events):
        kind = event.get('event')
        if kind not in {'generation.started', 'generation.finished', 'generation.failed', 'generation.detached'}:
            continue
        # Older failure receipts omitted the owner fields. Detached recovery
        # requires explicit root ownership; a child cannot settle its parent.
        default = session.get('id') if kind == 'generation.failed' else None
        if (event.get('sessionId', default) != session.get('id')
                or event.get('rootSessionId', default) != session.get('id')):
            continue
        generation = event.get('generation_id') or ('legacy', order)
        rank = generations.setdefault(generation, len(generations))
        identities = [event.get('initial_input_id')] if kind == 'generation.started' else event.get('input_ids', [])
        for identity in identities:
            if not isinstance(identity, str):continue
            if kind != 'generation.started' and identity in started_inputs and generation not in started_generations:
                # Retention may have dropped an older generation's start. A
                # late terminal receipt with unknown order cannot supersede
                # this input's observed admission merely by arriving later.
                continue
            previous = outcomes.get(identity)
            if previous and (previous[0] > rank or previous[0] == rank and (
                    previous[1].get('event') == 'generation.failed' or kind == 'generation.started')):
                continue
            outcomes[identity] = (rank, event)
    turns = []
    for turn in session.get('execution', {}).get('turns', []):
        receipt = outcomes.get(turn.get('inputId', turn.get('id')))
        event = receipt[1] if receipt else {}
        if event.get('event') == 'generation.failed':
            turn = {**turn, 'phase': 'error'}
        elif (event.get('event') == 'generation.detached'
                and (turn.get('status') or turn.get('phase')) in {'completed', 'complete', 'done', 'success', 'recorded'}):
            # Repair only a settled success/unknown display. Live turns still
            # await the host's Stop outcome. An explicit retry can reuse its
            # input ID: only the latest root generation owns that outcome.
            turn = {**turn, 'phase': 'interrupted'}
            turn.pop('status', None)
            ended = event.get('at')
            if isinstance(ended, (int, float)):turn['endedAt'] = ended
        turns.append(turn)
    return turns


def fail_generation(session, input_ids):
    """A manager failure supersedes an earlier idle receipt for its own inputs."""
    identities = {value for value in input_ids if isinstance(value, str)}
    for turn in session.get('execution', {}).get('turns', []):
        if turn.get('inputId', turn.get('id')) in identities:
            turn['phase'] = 'error'
            turn.setdefault('endedAt', time.time())


def finish(session,status='completed'):
    tree=session.get('execution',{})
    ended=time.time()
    independent = voice_usage_turns(tree)
    for turn in tree.get('turns',[]):
        if turn.get('phase') in LIVE_PHASES and turn['id'] not in independent:turn.update(phase=status,endedAt=ended)
    for node in tree.get('nodes',[]):
        # Foreground turn settlement cannot end separately scheduled naming
        # or delegated work. Background calls settle from their own provider
        # lifecycle, or after the host confirms their worker process exited.
        if node.get('kind') != 'worker' and node.get('sessionId') == node.get('rootSessionId') and node.get('phase') in LIVE_PHASES and not node.get('endedAt') and node.get('lifecycle')!='background' and not voice_usage_node(node):
            node.update(phase='interrupted' if status=='completed' else status,endedAt=ended)
    if 'nodes' in tree:refresh_usage(tree)


def finish_background(session,identities,status='interrupted'):
    """Settle only calls observed in the process whose exit was confirmed."""
    tree=session.get('execution',{});targets=set(identities);changed=False;ended=time.time()
    for node in tree.get('nodes',[]):
        if node['id'] in targets and node.get('lifecycle')=='background' and node.get('phase') in LIVE_PHASES and not node.get('endedAt'):
            node.update(phase=status,endedAt=ended);changed=True
    if changed:refresh_usage(tree)
