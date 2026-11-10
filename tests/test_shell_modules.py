from copy import deepcopy
import asyncio
import json

import pytest

from amplifier_web.service import AppService, AppError
from amplifier_web.shell_modules import DEFAULT, API, PROFILE
from test_chat_navigation import state_fixture, chat


@pytest.fixture
async def service(tmp_path):
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    app.state.update(state_fixture())
    app.state['view']['draft'] = 'Keep this unsent message'
    app.state['sessions'] = [chat('alpha'), chat('beta', 'two')]
    paths = {workspace['id']: workspace['path'] for workspace in app.state['workspaces']}
    for session in app.state['sessions']:
        session.update(workspace=paths[session['workspaceId']], messages=[])
    yield app
    await app.close()


async def settled_resources(app, identity):
    """Observe completed current pages before testing reuse of a warm snapshot."""
    import asyncio
    for _ in range(12):
        with app.clients.bind(identity):
            result = app.shell.resources.snapshot(identity)
        if not any(entry['pending'] for entry in result['instances'].values()):
            return result
        task = app.shell.resources.queries.runner
        assert task is not None, result
        await asyncio.wait_for(asyncio.shield(task), 15)
    raise AssertionError('Current shell resources did not settle')


async def command(app, name, **args):
    return await app.dispatch(name, args, origin='agent')


async def prepare(app, composition, client='browser-one', revision=0):
    result = await command(app, 'shell.changes.prepare', clientId=client, expectedRevision=revision, composition=composition)
    return result['result']['id']


async def stage(app, **overrides):
    manifest = {'id': 'test.navigator', 'version': '1.0.0', 'apiVersion': API, 'profile': PROFILE, 'stateSchema': 'navigation-v1', 'capabilities': ['navigation.read']}
    manifest.update(overrides)
    result = await command(app, 'shell.packages.stage', manifest=manifest, source='export default ({React})=>()=>React.createElement("p",null,"Hello")')
    return result['result']['digest']


def record_validation(app, digest):
    # Backend contract tests install a fixture receipt. The production browser
    # test exercises the real host subprocess and browser validation instead.
    record = app.shell.get('package', digest)
    record['validation'] = {'status': 'passed', 'hostFingerprint': app.shell.host_fingerprint()}
    app.shell.put('package', digest, record)


@pytest.mark.asyncio
async def test_independent_instances_clients_and_bounded_queries(service):
    composition = deepcopy(DEFAULT)
    composition['instances'].append({'id': 'beta', 'package': 'builtin.chats', 'slot': 'navigation', 'scope': {'mode': 'pinned', 'workspaceId': 'two'}})
    change = await prepare(service, composition)
    await command(service, 'shell.changes.apply', clientId='browser-one', changeId=change, expectedRevision=0)
    await command(service, 'shell.view.update', clientId='browser-one', instanceId='chats', patch={'navFilter': 'alpha'})
    before = deepcopy(service.state)
    first = service.shell.inspect('browser-one', snapshots=True)
    second = service.shell.inspect('browser-two', snapshots=True)
    assert first['snapshots']['chats']['chatNavigation']['scope']['filter'] == 'alpha'
    assert first['snapshots']['beta']['chatNavigation']['items'][0]['id'] == 'beta'
    assert second['snapshots']['chats']['chatNavigation']['scope']['filter'] == ''
    assert service.state == before
    assert 'sessions' not in first['snapshots']['chats']
    assert 'draft' not in first['snapshots']['chats']['view']
    assert service.state['view']['draft'] == 'Keep this unsent message'


@pytest.mark.asyncio
async def test_catalog_group_is_local_and_age_is_shared_per_client(service):
    service.clients.attach('browser-one')
    service.clients.attach('browser-two')
    composition = deepcopy(DEFAULT)
    composition['instances'].append({'id': 'other-chats', 'package': 'builtin.chats',
        'slot': 'navigation', 'scope': {'mode': 'all'}})
    change = await prepare(service, composition)
    await command(service, 'shell.changes.apply', clientId='browser-one', changeId=change, expectedRevision=0)
    before = deepcopy(service.state)
    await command(service, 'shell.view.update', clientId='browser-one', instanceId='chats',
                  patch={'navRecentView': {'navGroup': 'workspace'}, 'navGroup': 'status'})
    with service.clients.bind('browser-one'):
        await service.dispatch('view.update', {'patch': {'navAge': '7d'}})
        first = service.shell.inspect('browser-one', snapshots=True)['snapshots']
    assert first['chats']['sidebarNavigation']['search']['scope']['group'] == 'workspace'
    assert first['chats']['sidebarNavigation']['search']['scope']['age'] == '7d'
    assert first['chats']['sidebarNavigation']['workspace']['scope']['group'] == 'status'
    assert first['chats']['sidebarNavigation']['workspace']['scope']['age'] == '7d'
    assert first['other-chats']['sidebarNavigation']['search']['scope']['age'] == '7d'
    with service.clients.bind('browser-two'):
        second = service.shell.inspect('browser-two', snapshots=True)['snapshots']
    assert second['chats']['sidebarNavigation']['search']['scope']['group'] == 'recent'
    assert second['chats']['sidebarNavigation']['search']['scope']['age'] == 'all'
    saved = service.shell.get('client', 'browser-one')['views']['chats']['view']
    assert saved['navRecentView'] == {'navGroup': 'workspace'}
    assert service.state['sessions'] == before['sessions']
    assert service.state['view'] == before['view']
    assert service.state['selectedSessionId'] == before['selectedSessionId']


@pytest.mark.parametrize('surface,expected', [('chats', '24h'), ('workspace', '7d'), ('home', '7d')])
async def test_age_import_is_narrow_once_and_survives_reload(service, surface, expected):
    saved = service.clients.attach('old-client')
    saved['view'].pop('navAge')
    saved['view'].update(workSurface=surface, draft='Private draft', workHomeQuery='retained')
    saved['drafts'][saved.get('selectedSessionId') or ''] = 'Private draft'
    old_shell = service.shell.client('old-client')
    old_shell['views']['chats'] = {'view': {
        'navAge': '7d', 'navGroup': 'status',
        'navRecentView': {'navAge': '24h', 'navGroup': 'workspace', 'navFilter': 'kept'}}}
    service.shell.put('client', 'old-client', old_shell)
    reloaded = service.clients.attach('reload', resume='old-client')
    assert reloaded['view']['navAge'] == expected
    assert reloaded['view']['draft'] == 'Private draft'
    assert reloaded['view']['workHomeQuery'] == 'retained'
    assert service.shell.get('client', 'old-client') == old_shell
    reloaded['view']['navAge'] = 'all'
    assert service.clients.attach('reload')['view']['navAge'] == 'all'
    assert service.clients.attach('unrelated')['view']['navAge'] == 'all'


@pytest.mark.asyncio
async def test_prepare_preview_apply_report_revert_and_stale_revision(service):
    composition = deepcopy(DEFAULT)
    composition['instances'].reverse()
    composition['presentation'] = {'scheme': 'dark', 'layout': 'work', 'density': 'compact', 'accent': '#123456'}
    change = await prepare(service, composition)
    assert service.shell.client('browser-one')['composition'] == DEFAULT
    await command(service, 'shell.changes.preview', clientId='browser-one', changeId=change, expectedRevision=0)
    assert service.shell.client('browser-one')['preview']['composition'] == composition
    assert service.shell.client('browser-one')['composition'] == DEFAULT
    args = {'clientId': 'browser-one', 'changeId': change, 'expectedRevision': 0}
    first = await service.dispatch('shell.changes.apply', args, command_id='apply-one')
    retry = await service.dispatch('shell.changes.apply', args, command_id='apply-one')
    assert retry == first
    assert first['result']['status'] == 'awaiting-browser'
    with pytest.raises(AppError, match='changed'):
        await command(service, 'shell.changes.apply', **args)
    with pytest.raises(AppError, match='different arguments'):
        await service.dispatch('shell.changes.apply', {**args, 'expectedRevision': 1}, command_id='apply-one')
    await command(service, 'shell.report', clientId='browser-one', revision=1, instances={'workspaces': 'ready', 'chats': 'ready'})
    assert service.shell.client('browser-one')['lastGood'] == composition
    await command(service, 'shell.report', clientId='browser-one', revision=0, instances={'workspaces': 'error'})
    assert service.shell.client('browser-one')['reported']['status'] == 'ready'
    await command(service, 'shell.changes.revert', clientId='browser-one', changeId=change, expectedRevision=1)
    assert service.shell.client('browser-one')['composition'] == DEFAULT


@pytest.mark.asyncio
async def test_validation_digest_and_host_binding(service):
    digest = await stage(service)
    composition = deepcopy(DEFAULT)
    composition['instances'].append({'id': 'external', 'package': digest, 'slot': 'navigation'})
    with pytest.raises(AppError, match='validation'):
        await prepare(service, composition)
    record_validation(service, digest)
    change = await prepare(service, composition)
    path = service.shell.directory / (digest + '.mjs')
    path.write_text(path.read_text() + '\n// changed')
    with pytest.raises(AppError, match='contents changed'):
        await command(service, 'shell.changes.apply', clientId='browser-one', changeId=change, expectedRevision=0)
    assert service.shell.client('browser-one')['composition'] == DEFAULT


@pytest.mark.asyncio
async def test_stale_host_receipt_rejected(service, monkeypatch):
    digest = await stage(service)
    record_validation(service, digest)
    monkeypatch.setattr(service.shell, 'host_fingerprint', lambda: 'different-host')
    with pytest.raises(AppError, match='validation'):
        service.shell.manifest(digest)


@pytest.mark.asyncio
async def test_dirty_removal_and_incompatible_replacement_defer(service):
    await command(service, 'shell.view.update', clientId='browser-one', instanceId='workspaces', patch={'workspaceDraft': {'mode': 'add', 'path': '/keep/me'}})
    composition = deepcopy(DEFAULT)
    composition['instances'] = composition['instances'][1:]
    change = await prepare(service, composition)
    result = await command(service, 'shell.changes.apply', clientId='browser-one', changeId=change, expectedRevision=0)
    assert not result['accepted'] and result['result']['status'] == 'deferred'
    assert service.shell.client('browser-one')['views']['workspaces']['view']['workspaceDraft']['path'] == '/keep/me'
    digest = await stage(service, stateSchema='incompatible-v2')
    record_validation(service, digest)
    composition = deepcopy(DEFAULT)
    composition['instances'][1]['package'] = digest
    change = await prepare(service, composition)
    result = await command(service, 'shell.changes.apply', clientId='browser-one', changeId=change, expectedRevision=0)
    assert result['result']['status'] == 'deferred'
    assert service.shell.client('browser-one')['composition'] == DEFAULT


@pytest.mark.asyncio
async def test_namespaces_and_capabilities(service):
    with pytest.raises(AppError, match='Unsupported'):
        await command(service, 'shell.view.update', clientId='browser-one', instanceId='chats', patch={'draft': 'overwrite'})
    with pytest.raises(AppError, match='capability'):
        await command(service, 'shell.command', clientId='browser-one', instanceId='chats', action='runtime.control', args={})
    with pytest.raises(AppError, match='reserved'):
        await stage(service, id='builtin.custom')
    assert 'shell.packages.validate' in {row['name'] for row in service.get_actions()}


@pytest.mark.asyncio
async def test_recovery_and_saved_composition_survive_restart(service):
    composition = deepcopy(DEFAULT)
    composition['instances'] = []
    change = await prepare(service, composition)
    await command(service, 'shell.changes.apply', clientId='browser-one', changeId=change, expectedRevision=0)
    await service.close()
    restored = AppService(service.data_dir, workspace=service.default_workspace)
    try:
        assert restored.shell.client('browser-one')['composition'] == composition
        await command(restored, 'shell.recover', clientId='browser-one', target='default', expectedRevision=1)
        assert restored.shell.client('browser-one')['composition'] == DEFAULT
    finally:
        restored.db.close()


@pytest.mark.asyncio
async def test_invalid_structure_rejected_before_commit(service):
    bad = deepcopy(DEFAULT)
    bad['instances'].append(deepcopy(bad['instances'][0]))
    with pytest.raises(AppError, match='unique'):
        await prepare(service, bad)
    bad = deepcopy(DEFAULT)
    bad['presentation']['css'] = 'body{display:none}'
    with pytest.raises(AppError, match='Additional properties'):
        await prepare(service, bad)
    assert service.shell.client('browser-one')['revision'] == 0


@pytest.mark.asyncio
async def test_unknown_package_unsupported_api_and_invalid_form_rejected(service):
    composition = deepcopy(DEFAULT)
    composition['instances'][0]['package'] = 'unavailable'
    with pytest.raises(AppError, match='Unknown shell package'):
        await prepare(service, composition)
    with pytest.raises(AppError, match='expected'):
        await stage(service, apiVersion='99.0')
    for value in [42, {'mode': 'unexpected'}, {'name': 'x' * 201}]:
        with pytest.raises(AppError):
            await command(service, 'shell.view.update', clientId='browser-one', instanceId='workspaces', patch={'workspaceDraft': value})


@pytest.mark.asyncio
async def test_pin_resets_only_originating_page_and_new_clients_keep_own_defaults(service):
    service.state['sessions'] = [{**chat(str(index)), 'workspace': '/projects/one/shared', 'messages': []} for index in range(250)]
    service.shell.client('browser-two')
    scope = service.shell.inspect('browser-one', snapshots=True)['snapshots']['chats']['chatNavigation']['scope']
    await command(service, 'shell.view.update', clientId='browser-one', instanceId='chats', patch={'navChatPage': {**scope, 'index': 2}})
    service.state['view']['navFilter'] = 'a changed legacy default'
    assert service.shell.inspect('browser-two', snapshots=True)['snapshots']['chats']['chatNavigation']['total'] == 250
    # The canonical command handler performs pinning; shell.command restores
    # the expected local reveal behavior without changing another client.
    await command(service, 'shell.command', clientId='browser-one', instanceId='chats', action='session.pin', args={'id': '249', 'pinned': True})
    assert service.shell.inspect('browser-one', snapshots=True)['snapshots']['chats']['chatNavigation']['index'] == 0
    assert service.shell.inspect('browser-two', snapshots=True)['snapshots']['chats']['chatNavigation']['scope']['filter'] == ''


@pytest.mark.asyncio
async def test_corrupt_optional_package_does_not_block_recovery_query(service):
    digest = await stage(service)
    record_validation(service, digest)
    composition = deepcopy(DEFAULT)
    composition['instances'].append({'id': 'optional', 'package': digest, 'slot': 'navigation'})
    change = await prepare(service, composition)
    await command(service, 'shell.changes.apply', clientId='browser-one', changeId=change, expectedRevision=0)
    (service.shell.directory / (digest + '.mjs')).unlink()
    state = service.shell.inspect('browser-one', snapshots=True)
    assert state['packages'][digest]['error']
    assert 'optional' not in state['snapshots']
    recovery = service.shell.inspect('browser-one', snapshots=True, recovery=True)
    assert recovery['effectiveComposition'] == DEFAULT
    assert set(recovery['snapshots']) == {row['id'] for row in recovery['resolvedInstances']}
    assert all(row['package'].startswith('builtin.') for row in recovery['resolvedInstances'])

@pytest.mark.asyncio
async def test_paired_workspace_drill_in_is_shared_with_agents_and_client_scoped(service):
    paths = {workspace['id']: workspace['path'] for workspace in service.state['workspaces']}
    for session in service.state['sessions']:
        session['workspace'] = paths[session['workspaceId']]
    for client in ('browser-one', 'browser-two'):
        await command(service, 'shell.view.update', clientId=client, instanceId='chats',
                      patch={'navWorkspaceList': True, 'navStatusFilter': 'attention'})
    await command(service, 'shell.command', clientId='browser-one', instanceId='workspaces',
                  action='workspace.select', args={'id': 'two'})
    first = (await command(service, 'shell.query', clientId='browser-one', instanceId='chats'))['result']
    second = (await command(service, 'shell.query', clientId='browser-two', instanceId='chats'))['result']
    assert first['view']['navWorkspaceList'] is False
    assert first['view']['navStatusFilter'] == 'all'
    assert second['view']['navWorkspaceList'] is True
    assert second['view']['navStatusFilter'] == 'attention'


@pytest.mark.asyncio
async def test_shell_summaries_derive_current_unread_without_acknowledging_it(service):
    session = service.state['sessions'][0]
    session['completion'] = {'id': 'fresh', 'at': 123}
    before = deepcopy(service.state.get('attentionRead', {}))
    result = (await command(service, 'shell.query', clientId='browser-one', instanceId='chats'))['result']
    row = next(row for row in result['chatNavigation']['items'] if row['id'] == session['id'])
    assert row['activity']['kind'] == 'unread'
    assert result['attention']['sessions'][session['id']] == 1
    assert service.state.get('attentionRead', {}) == before


async def test_error_review_exposes_only_bounded_receipts_and_uses_shared_action(service):
    composition = deepcopy(DEFAULT)
    composition['instances'].append({'id': 'limited', 'package': 'builtin.chats', 'slot': 'navigation',
                                     'scope': {'mode': 'pinned', 'workspaceId': 'one'}})
    change = await prepare(service, composition)
    await command(service, 'shell.changes.apply', clientId='browser-one', changeId=change, expectedRevision=0)
    for session in service.state['sessions']:
        session.update(error='Private fixture failure detail', errorAt=1, status='error')
    service._publish()
    snapshot = (await command(service, 'shell.query', clientId='browser-one', instanceId='limited'))['result']
    receipt, = snapshot['attention']['items']
    unread_key = service.projections.shell_key(service.state)
    assert set(receipt) == {'id', 'sessionId', 'fingerprint', 'read'}
    assert receipt['id'] == 'session:alpha' and not receipt['read']
    assert 'Private fixture failure detail' not in json.dumps(snapshot)
    before = deepcopy(service.state['sessions'])
    args = {'ids': [receipt['id']], 'fingerprints': {receipt['id']: receipt['fingerprint']}}
    await command(service, 'shell.command', clientId='browser-one', instanceId='limited',
                  action='attention.read', args=args)
    after = (await command(service, 'shell.query', clientId='browser-one', instanceId='limited'))['result']
    assert after['attention']['items'][0]['read']
    assert after['chatNavigation']['items'][0]['activity']['kind'] == 'idle'
    assert service.state['sessions'] == before
    # A later same-text occurrence is not covered by the previously observed receipt.
    service.state['sessions'][0]['errorAt'] = 2
    service._publish()
    await command(service, 'shell.command', clientId='browser-one', instanceId='limited',
                  action='attention.read', args=args)
    after = (await command(service, 'shell.query', clientId='browser-one', instanceId='limited'))['result']
    assert not after['attention']['items'][0]['read']
    assert after['chatNavigation']['items'][0]['activity']['kind'] == 'attention'
    distinct_key = service.projections.shell_key(service.state)
    assert distinct_key != unread_key
    service.state['sessions'][0]['errorAt'] = 3
    service._publish()
    assert service.projections.shell_key(service.state) != distinct_key
    for invalid in ({'ids': ['session:beta'], 'fingerprints': {'session:beta': receipt['fingerprint']}},
                    {'ids': ['approval:fixture'], 'fingerprints': {'approval:fixture': receipt['fingerprint']}},
                    {'ids': [receipt['id']]}, {'ids': [receipt['id']], 'fingerprints': {}}):
        with pytest.raises(AppError, match='Only observed conversation notices'):
            await command(service, 'shell.command', clientId='browser-one', instanceId='limited',
                          action='attention.read', args=invalid)


async def test_read_state_uses_exact_bounded_receipts_without_reviewing_decisions(service):
    row = service.state['sessions'][0]
    row.update(manualUnread='first-mark', completion={'id': 'first-response', 'at': 1},
               approvals=[{'id': 'keep-approval', 'status': 'pending', 'title': 'Permission'}],
               error='Keep saved error', errorAt=1)
    service._publish()
    snapshot = (await command(service, 'shell.query', clientId='browser-one', instanceId='chats'))['result']
    receipts = [item for item in snapshot['attention']['items']
                if item['id'] in {'unread:alpha', 'completion:alpha'}]
    assert len(receipts) == 2
    assert all(set(item) == {'id', 'sessionId', 'fingerprint', 'read'} for item in receipts)
    observed = {'ids': [item['id'] for item in receipts],
                'fingerprints': {item['id']: item['fingerprint'] for item in receipts}}
    before = deepcopy(service.state['sessions'])
    await command(service, 'shell.command', clientId='browser-one', instanceId='chats',
                  action='attention.read', args=observed)
    assert service.state['sessions'] == before
    notices = service.projections.attention(service.state)['items']
    assert all(item['read'] for item in notices if item['id'] in observed['ids'])
    assert all(not item['read'] for item in notices if item['id'] not in observed['ids'])
    row.update(manualUnread='later-mark', completion={'id': 'later-response', 'at': 2})
    service._publish()
    await command(service, 'shell.command', clientId='browser-one', instanceId='chats',
                  action='attention.read', args=observed)
    notices = service.projections.attention(service.state)['items']
    assert all(not item['read'] for item in notices if item['id'] in observed['ids'])


async def test_read_state_accepts_observed_later_status_page_only(service):
    from amplifier_web.shell_resources import scope_for
    workspace = service.state['workspaces'][0]['path']
    service.state['sessions'] = [
        {**chat(f'ready-{index:03}'), 'workspace': workspace, 'manualUnread': f'mark-{index}',
         'recentActivityAt': 1000 + index, 'messages': []} for index in range(75)]
    service._publish()
    identity = 'status-reader'
    service.clients.attach(identity)
    with service.clients.bind(identity):
        await command(service, 'view.update', patch={'workSurface': 'home', 'navStatusPages': {'unread': 1}})
        service.shell.resources.register(identity, 1, ['workStatusUnread'], scope_for(service.state))
        snapshot = await settled_resources(service, identity)
        resources = snapshot['instances']['chats']['resources']
        page = resources['workStatusUnread']['value']['workStatusUnread']
        assert page['index'] == 1 and page['items']
        receipt = resources['context']['value']['attention']['items'][0]
        assert receipt['sessionId'] in {row['id'] for row in page['items']}
        args = {'ids': [receipt['id']], 'fingerprints': {receipt['id']: receipt['fingerprint']}}
        await command(service, 'shell.command', clientId=identity, instanceId='chats',
                      action='attention.read', args=args)
        assert next(item for item in service.projections.attention(service.state)['items']
                    if item['id'] == receipt['id'])['read']
        invisible = next(row for row in service.state['sessions'] if row['id'] not in {item['id'] for item in page['items']})
        with pytest.raises(AppError, match='Only observed conversation notices'):
            await command(service, 'shell.command', clientId=identity, instanceId='chats',
                          action='attention.read', args={'ids': ['unread:' + invisible['id']],
                          'fingerprints': {'unread:' + invisible['id']: 'not-observed'}})


async def test_read_state_preserves_registered_custom_navigation_receipts(service, monkeypatch):
    from amplifier_web.shell_resources import scope_for
    identity = 'custom-reader'
    digest = await stage(service, capabilities=['navigation.read', 'chats.manage'])
    record_validation(service, digest)
    composition = deepcopy(DEFAULT)
    composition['instances'].append({'id': 'custom', 'package': digest, 'slot': 'navigation',
                                     'scope': {'mode': 'pinned', 'workspaceId': 'one'}})
    change = await prepare(service, composition, client=identity)
    await command(service, 'shell.changes.apply', clientId=identity, changeId=change, expectedRevision=0)
    for session in service.state['sessions']:
        session['manualUnread'] = 'first-mark'
    service._publish()
    service.clients.attach(identity)
    with service.clients.bind(identity):
        service.shell.resources.register(identity, 1, ['recentNavigation'], scope_for(service.state))
        snapshot = await settled_resources(service, identity)
        entry = snapshot['instances']['custom']
        assert set(entry['resources']) == {'customSnapshot'}
        receipt, = entry['resources']['customSnapshot']['value']['attention']['items']
        assert receipt['id'] == 'unread:alpha' and not receipt['read']
        args = {'ids': [receipt['id']], 'fingerprints': {receipt['id']: receipt['fingerprint']}}
        target = {'clientId': identity, 'instanceId': 'custom', 'generation': entry['generation'],
                  'action': 'attention.read'}
        before = deepcopy(service.state['sessions'])
        await command(service, 'shell.command', **target, args=args)
        assert service.state['sessions'] == before
        notices = {item['id']: item for item in service.projections.attention(service.state)['items']}
        assert notices['unread:alpha']['read'] and not notices['unread:beta']['read']
        with pytest.raises(AppError, match='Only observed conversation notices'):
            await command(service, 'shell.command', **target, args={
                'ids': ['unread:beta'], 'fingerprints': {'unread:beta': notices['unread:beta']['fingerprint']}})
        service.state['sessions'][0]['manualUnread'] = 'newer-mark'
        service._publish()
        await command(service, 'shell.command', **target, args=args)
        notices = {item['id']: item for item in service.projections.attention(service.state)['items']}
        assert not notices['unread:alpha']['read']
        with pytest.raises(AppError, match='component was replaced'):
            await command(service, 'shell.command', **{**target, 'generation': entry['generation'] + 1}, args=args)
        with monkeypatch.context() as patch:
            patch.setattr(service.shell.resources, 'snapshot', lambda _: {'instances': {}})
            with pytest.raises(AppError, match='Only observed conversation notices'):
                await command(service, 'shell.command', **target, args=args)


async def test_sort_and_pin_order_use_shared_commands_and_keep_other_views(service):
    for session in service.state['sessions']:
        session.update(workspace=str(service.default_workspace), messages=[])
    await command(service,'session.pin',id='alpha',pinned=True)
    await command(service,'session.pin',id='beta',pinned=True)
    await command(service,'shell.view.update',clientId='browser-one',instanceId='chats',patch={'navSort':'name','navChatScope':'all'})
    await command(service,'shell.command',clientId='browser-one',instanceId='chats',action='session.pinOrder',args={'ids':['beta','alpha']})
    first=service.shell.inspect('browser-one',snapshots=True)['snapshots']['chats']
    second=service.shell.inspect('browser-two',snapshots=True)['snapshots']['chats']
    assert first['pinnedSessionIds']==second['pinnedSessionIds']==['beta','alpha']
    assert first['chatNavigation']['scope']['sort']=='name'
    assert 'sort' not in second['chatNavigation']['scope']
    assert [r['id'] for r in first['chatNavigation']['items']]==['beta','alpha']
    with pytest.raises(AppError,match='every current ID'):
        await command(service,'session.pinOrder',ids=['alpha'])


async def test_committed_appearance_survives_new_window_recovery_and_restart(service):
    from amplifier_web.shell_modules import ShellModules
    composition = deepcopy(DEFAULT)
    composition['presentation'] = {'scheme': 'light', 'interfaceDetail': 'minimal'}
    change = await prepare(service, composition)
    await command(service, 'shell.changes.apply', clientId='browser-one', changeId=change, expectedRevision=0)
    service.shell = ShellModules(service)
    assert service.shell.client('fresh-window')['composition']['presentation'] == composition['presentation']
    dark = deepcopy(composition)
    dark['presentation']['scheme'] = 'dark'
    change = await prepare(service, dark, revision=1)
    await command(service, 'shell.changes.preview', clientId='browser-one', changeId=change, expectedRevision=1)
    assert service.shell.client('during-preview')['composition']['presentation']['scheme'] == 'light'
    await command(service, 'shell.recover', clientId='browser-one', target='default', expectedRevision=1)
    assert service.shell.client('browser-one')['composition']['presentation']['scheme'] == 'light'


async def test_existing_tab_appearance_migrates_without_inheriting_content(service):
    from amplifier_web.shell_modules import ShellModules
    saved = service.shell.client('legacy')
    saved['composition']['presentation'] = {'scheme': 'light'}
    saved['baselineView']['navFilter'] = 'private tab navigation'
    service.shell.put('client', 'legacy', saved)
    service.shell = ShellModules(service)
    fresh = service.shell.client('new')
    assert fresh['composition']['presentation'] == {'scheme': 'light'}
    assert fresh['baselineView'].get('navFilter') != 'private tab navigation'


async def test_nested_shell_command_does_not_hold_owner_lock_or_overwrite_later_view(service, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    original = service.dispatch

    async def delayed(action, args=None, **kwargs):
        if action == 'session.pin':
            assert not service.lock.locked()
            entered.set()
            await release.wait()
        return await original(action, args, **kwargs)

    monkeypatch.setattr(service, 'dispatch', delayed)
    args = {'clientId': 'browser-one', 'instanceId': 'chats',
            'action': 'session.pin', 'args': {'id': 'alpha', 'pinned': True}}
    pending = asyncio.create_task(service.dispatch('shell.command', args, command_id='nested-pin'))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await command(service, 'shell.view.update', clientId='browser-one', instanceId='chats',
                      patch={'navFilter': 'later-filter'})
        # Recovery replaces the module generation while the downstream request
        # waits. Its completed effect must retain its receipt, not a false 409.
        await command(service, 'shell.recover', clientId='browser-one', expectedRevision=0, target='default')
        assert service.shell.client('browser-one')['views']['chats']['generation'] == 1
        release.set()
        result = await asyncio.wait_for(pending, 5)
        assert result['accepted']
        assert service.shell.client('browser-one')['views']['chats']['view']['navFilter'] == 'later-filter'
        assert service.shell.client('browser-one')['views']['chats']['generation'] == 1
        assert 'navChatPage' not in service.shell.client('browser-one')['views']['chats']['view']
        saved = service.shell.get('command', 'nested-pin')
        assert saved['receipt']['accepted'] and 'state' not in saved['receipt']
        await service.dispatch('session.rename', {'id': 'alpha', 'title': 'Updated after pin'})
        retry = await service.dispatch('shell.command', args, command_id='nested-pin')
        assert retry['accepted'] and retry['state']['revision'] == service.browser_state()['revision']
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


async def test_presentation_command_uses_current_preview_in_one_owner_unit(service):
    preview = deepcopy(DEFAULT)
    preview['presentation'] = {'scheme': 'dark', 'density': 'compact'}
    change = await prepare(service, preview)
    await command(service, 'shell.changes.preview', clientId='browser-one', changeId=change, expectedRevision=0)
    args = {'clientId': 'browser-one', 'instanceId': 'core.settings.appearance', 'generation': 0,
            'action': 'presentation.update', 'args': {'expectedRevision': 0, 'patch': {'scheme': 'light'}}}
    result = await asyncio.wait_for(service.dispatch('shell.command', args, command_id='presentation-owner'), 5)
    assert result['accepted']
    client = service.shell.client('browser-one')
    assert client['preview'] is None and client['revision'] == 1
    assert client['composition']['presentation'] == {'scheme': 'light', 'density': 'compact'}
    assert await service.dispatch('shell.command', args, command_id='presentation-owner') == result


@pytest.mark.parametrize('change', ['package', 'host'])
async def test_validation_external_await_rechecks_package_and_host_under_owner_lock(
        service, tmp_path, monkeypatch, change):
    import shutil
    from amplifier_web import shell_modules
    digest = await stage(service)
    toolchain = tmp_path / 'node-modules'
    (toolchain / 'playwright').mkdir(parents=True)
    monkeypatch.setenv('AMPLIFIER_SHELL_NODE_MODULES', str(toolchain))
    monkeypatch.setattr(shutil, 'which', lambda name: 'synthetic-node')
    monkeypatch.setattr(service.shell, 'host_fingerprint', lambda: 'host-before')
    entered, release = asyncio.Event(), asyncio.Event()

    class Process:
        returncode = 0

        async def communicate(self):
            assert not service.lock.locked()
            entered.set()
            await release.wait()
            return b'{"status":"passed"}', b''

    async def subprocess(*args, **kwargs):
        assert not service.lock.locked()
        return Process()

    monkeypatch.setattr(shell_modules.asyncio, 'create_subprocess_exec', subprocess)
    pending = asyncio.create_task(service.shell.validate_package(digest))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert (await command(service, 'shell.query', clientId='browser-one', instanceId='chats'))['accepted']
        async with service.lock:
            if change == 'package':
                record = service.shell.get('package', digest)
                record['validation'] = {'status': 'superseded'}
                service.shell.put('package', digest, record)
            else:
                monkeypatch.setattr(service.shell, 'host_fingerprint', lambda: 'host-after')
        release.set()
        with pytest.raises(AppError, match='changed'):
            await asyncio.wait_for(pending, 5)
        assert service.shell.get('package', digest)['validation'] != {'status': 'passed'}
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
