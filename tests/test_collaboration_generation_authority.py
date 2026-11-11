"""Host current-work checks; native producer integration is qualified separately."""
import copy
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from amplifier_web.collaboration import BINDING, PRINCIPAL
from amplifier_web.service import AppError
from test_coordination_generation import app


@contextmanager
def bound(source, *, principal=None, generation=None, inputs=None):
    principal = principal or source['id']
    active = source['collaborationGeneration']
    p = PRINCIPAL.set(principal)
    b = BINDING.set({'_runtimeSessionId': principal,
        '_generationId': generation or active['id'],
        '_inputBindings': [{'inputId': value} for value in
            (active['inputIds'] if inputs is None else inputs)]})
    try:
        yield
    finally:
        BINDING.reset(b)
        PRINCIPAL.reset(p)


async def started(app, source, identity, **extra):
    await app.on_runtime_event('runtime.generation', {'sessionId': source['id'],
        'rootSessionId': source['id'], 'event': 'generation.started',
        'generation_id': identity, **extra})


async def test_stop_invalidates_work_before_terminal_and_duplicate_start_cannot_renew(app):
    source = app.state['sessions'][0]
    await started(app, source, 'admitted', interruptionRevision=0)
    app.collaboration.observe(source, 'runtime.status', {'event': 'input.delivered', 'inputId': 'system-input'})
    original = copy.deepcopy(source['collaborationGeneration'])
    with bound(source):
        assert app.collaboration.lineage(source, 'agent')['sourceInputIds'] == ['system-input']
        await app.dispatch('conversation.stop', {'sessionId': source['id']})
        assert not source['collaborationGeneration']['terminal']
        with pytest.raises(AppError, match='Stop'):
            app.collaboration.active_binding(source)
        # Even an incorrectly refreshed epoch cannot make an old ID fresh.
        await started(app, source, 'admitted', interruptionRevision=source['interruptionRevision'])
        assert source['collaborationGeneration'] == original
        with pytest.raises(AppError, match='Stop'):
            app.collaboration.lineage(source, 'agent')


@pytest.mark.parametrize('epoch', [None, 0, -1, True, '1', 2])
async def test_first_seen_start_cannot_acquire_authority_after_stop_without_exact_epoch(app, epoch):
    source = app.state['sessions'][0]
    await app.dispatch('conversation.stop', {'sessionId': source['id']})
    original = copy.deepcopy(source['collaborationGeneration'])
    await started(app, source, 'delayed-first-start', **({} if epoch is None else {'interruptionRevision': epoch}))
    assert source['collaborationGeneration'] == original
    with bound(source, generation='delayed-first-start'):
        with pytest.raises(AppError, match='current transport-bound'):
            app.collaboration.active_binding(source)


async def test_exact_new_epoch_accepts_new_work_without_reviving_old_starts_or_transport(app):
    source = app.state['sessions'][0]
    await started(app, source, 'old', interruptionRevision=0)
    app.collaboration.observe(source, 'runtime.status', {'event': 'input.delivered', 'inputId': 'old-input'})
    await app.dispatch('conversation.stop', {'sessionId': source['id']})
    await started(app, source, 'fresh', interruptionRevision=source['interruptionRevision'])
    app.collaboration.observe(source, 'runtime.status', {'event': 'input.delivered', 'inputId': 'fresh-system-input'})
    current = copy.deepcopy(source['collaborationGeneration'])
    await started(app, source, 'old', interruptionRevision=source['interruptionRevision'])
    assert source['collaborationGeneration'] == current
    with bound(source, generation='old', inputs=['old-input']):
        with pytest.raises(AppError, match='current transport-bound'):
            app.collaboration.active_binding(source)
    with bound(source):
        lineage = app.collaboration.lineage(source, 'agent')
        assert lineage == {'sourceGenerationId': 'fresh', 'sourceInputIds': ['fresh-system-input'],
            'sourceInterruptionRevision': source['interruptionRevision']}


async def test_descendant_opt_in_retains_actor_and_assigned_inputs_without_root_reply_identity(app):
    source = app.state['sessions'][0]
    await started(app, source, 'owner-generation', interruptionRevision=0)
    source['collaborationGeneration']['inputIds'] = ['assigned-system-input', 'other-input']
    with bound(source, principal='native-child', inputs=['assigned-system-input']):
        with pytest.raises(AppError, match='root generation'):
            app.collaboration.active_binding(source)
        with pytest.raises(AppError, match='root generation'):
            app.collaboration.lineage(source, 'agent')
        binding, active = app.collaboration.active_binding(source, allow_descendant=True)
        assert PRINCIPAL.get() == 'native-child'
        assert binding['_inputBindings'] == [{'inputId': 'assigned-system-input'}]
        assert active['id'] == 'owner-generation'
        source['interruptionRevision'] = 1
        with pytest.raises(AppError, match='Stop'):
            app.collaboration.active_binding(source, allow_descendant=True)


async def test_terminal_generation_start_does_not_reopen_authority(app):
    source = app.state['sessions'][0]
    await started(app, source, 'finished', interruptionRevision=0)
    source['collaborationGeneration'].update(terminal=True, completed=True)
    original = copy.deepcopy(source['collaborationGeneration'])
    await started(app, source, 'finished', interruptionRevision=0)
    assert source['collaborationGeneration'] == original
    with bound(source):
        with pytest.raises(AppError, match='current transport-bound'):
            app.collaboration.active_binding(source)


@pytest.mark.parametrize('fault', ['none', 'missing-origin', 'foreign-root', 'foreign-child',
                                  'generation', 'owner', 'stop'])
async def test_supplied_child_origin_uses_canonical_owner_without_root_fallback(app, fault):
    source = app.state['sessions'][0]
    await started(app, source, 'current-root', interruptionRevision=0)
    source['collaborationGeneration']['inputIds'] = ['assigned-input']
    native = source.get('runtimeSessionId') or source.get('nativeIdentity') or source['id']
    app.runtime.workers = {source['id']: {'process': SimpleNamespace(returncode=None),
                                         'runtimeOwnerId': 'current-process'}}
    with bound(source, principal='native-child'):
        binding = BINDING.get()
        origin = {'rootSessionId': native, 'runtimeSessionId': 'native-child',
                  'generationId': 'current-root', 'inputIds': ['assigned-input'], 'runId': 'child-run',
                  **{key: source.get(key, 0) for key in
                     ('interruptionRevision', 'executionRevision', 'configurationPendingRevision')}}
        binding.update(_childOrigin=origin, _runtimeOwnerId='current-process')
        if fault == 'missing-origin': binding['_childOrigin'] = None
        elif fault == 'foreign-root': origin['rootSessionId'] = 'foreign-root'
        elif fault == 'foreign-child': origin['runtimeSessionId'] = 'foreign-child'
        elif fault == 'generation': origin['generationId'] = 'foreign-generation'
        elif fault == 'owner': binding['_runtimeOwnerId'] = 'replaced-process'
        elif fault == 'stop': source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
        # Root reply/provenance still cannot borrow even a valid child origin.
        with pytest.raises(AppError, match='root generation'):
            app.collaboration.active_binding(source)
        if fault == 'none':
            _, active = app.collaboration.active_binding(source, allow_descendant=True)
            assert active['id'] == 'current-root' and active['inputIds'] == ['assigned-input']
        else:
            with pytest.raises(AppError, match='native child execution'):
                app.collaboration.active_binding(source, allow_descendant=True)


@pytest.mark.parametrize(('task_status', 'accepted'), [('completed', True), ('paused', False)])
async def test_child_execution_ignores_completed_history_but_honors_paused_task(app, monkeypatch, task_status, accepted):
    from amplifier_web.child_execution import current

    source = app.state['sessions'][0]
    source['status'] = 'working'
    await started(app, source, 'fresh-generation', interruptionRevision=0)
    source['collaborationGeneration']['inputIds'] = ['fresh-input']
    native = source.get('runtimeSessionId') or source.get('nativeIdentity') or source['id']
    app.runtime.workers = {source['id']: {'process': SimpleNamespace(returncode=None),
                                         'runtimeOwnerId': 'current-process'}}
    monkeypatch.setattr(app.coordination, 'task', lambda _sid: {'status': task_status})
    origin = {'rootSessionId': native, 'runtimeSessionId': 'native-child',
              'generationId': 'fresh-generation', 'inputIds': ['fresh-input'], 'runId': 'fresh-run',
              **{key: source.get(key, 0) for key in
                 ('interruptionRevision', 'executionRevision', 'configurationPendingRevision')}}
    with bound(source, principal='native-child'):
        binding = BINDING.get()
        binding.update(_childOrigin=origin, _runtimeOwnerId='current-process')
        assert (current(app, source) == origin) is accepted
        if not accepted:
            _child, reason = current(app, source, explain=True)
            assert 'task is paused' in reason


async def test_child_execution_explains_stale_runtime_owner(app):
    from amplifier_web.child_execution import current

    source = app.state['sessions'][0]
    source['status'] = 'working'
    await started(app, source, 'fresh-generation', interruptionRevision=0)
    source['collaborationGeneration']['inputIds'] = ['fresh-input']
    native = source.get('runtimeSessionId') or source.get('nativeIdentity') or source['id']
    app.runtime.workers = {source['id']: {'process': SimpleNamespace(returncode=None),
                                         'runtimeOwnerId': 'replacement-process'}}
    origin = {'rootSessionId': native, 'runtimeSessionId': 'native-child',
              'generationId': 'fresh-generation', 'inputIds': ['fresh-input'], 'runId': 'fresh-run',
              **{key: source.get(key, 0) for key in
                 ('interruptionRevision', 'executionRevision', 'configurationPendingRevision')}}
    with bound(source, principal='native-child'):
        binding = BINDING.get()
        binding.update(_childOrigin=origin, _runtimeOwnerId='original-process')
        child, reason = current(app, source, explain=True)
        assert child is None and 'runtime owner changed' in reason
