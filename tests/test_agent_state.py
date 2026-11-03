import copy
import json
import pytest
from amplifier_web.agent_state import read_state
from amplifier_web.service import AppService


def test_scoped_overview_keeps_other_chat_content_out_and_pages_are_lossless():
    state={'revision':8,'selectedSessionId':'other','sessions':[{'id':'current','title':'New','messages':[{'role':'user','text':'Show a diagram'}]}, {'id':'other','title':'Old','messages':[{'text':'OLD_CHAT_PRIVATE_CONTENT'*10000}]}], 'canvas':{'content':'A'*40000}, 'escaped/key':{'~test':'abc'}}
    preview=read_state(state,{},session_id='current')
    assert preview['session']['id']=='current'
    assert 'OLD_CHAT_PRIVATE_CONTENT' not in json.dumps(preview)
    assert len(json.dumps(preview))<20000
    text='';offset=0
    while True:
        page=read_state(state,{'path':'/canvas/content','offset':offset,'revision':8})
        text+=page['value'];offset=page['nextOffset']
        if offset is None:break
    assert text==state['canvas']['content']
    assert read_state(state,{'path':'/escaped~1key/~0test'})['value']=='abc'
    with pytest.raises(ValueError,match='changed'):read_state(state,{'path':'/canvas','revision':7})
    with pytest.raises(ValueError):read_state(state,{'path':'/not-here'})


async def test_configuration_deduplication_and_on_demand_provenance_survive_restart(tmp_path):
    app=AppService(tmp_path,workspace=tmp_path)
    await app.dispatch('session.create',{})
    sid=app.state['selectedSessionId']
    provenance={'agents':[{'name':f'agent-{i}','include_paths':['foundation/path/'+('x'*1000)]*10} for i in range(100)]}
    config={'plan':{'tools':[{'module':'tool-fixture','config':{'limit':2}}]},'provenance':provenance}
    app.state['sessions'][0]['configuration']=copy.deepcopy(config)
    app.state['sessionConfiguration']={sid:copy.deepcopy(config)}
    app.state['runtimeControl']={sid:{'configuration.inspect':copy.deepcopy(config)}}
    before=len(json.dumps(app.state));app._save()
    assert len(json.dumps(app.state))<before/10
    assert 'sessionConfiguration' not in app.state
    assert app.state['sessions'][0]['configuration']['plan']==config['plan']
    assert app.state['runtimeControl'][sid]['configuration.inspect']=={'configurationSessionId':sid}
    path='/sessions/0/configuration/provenance/agents/99/include_paths/0'
    result=await app.app_bridge('get_state',{'path':path},sid)
    assert result['value']==provenance['agents'][99]['include_paths'][0]
    dispatched=await app.app_bridge('dispatch',{'action':'canvas.show','args':{'kind':'text','content':'hello'}},sid)
    assert len(json.dumps(dispatched))<30000 and dispatched['state']['canvas']['content']=='hello'
    actions=await app.app_bridge('list_actions',{'prefix':'canvas.'},sid)
    assert actions['items'] and all(a['name'].startswith('canvas.') for a in actions['items'])
    await app.close()
    app=AppService(tmp_path,workspace=tmp_path)
    assert (await app.app_bridge('get_state',{'path':path},sid))['value']==provenance['agents'][99]['include_paths'][0]
    await app.close()


def test_native_history_overview_explains_lazy_messages_and_agent_controls():
    state={'revision':1,'selectedSessionId':'native','sharedHistory':{'loading':False,'projectCount':1,'sessionCount':1},
           'sessions':[{'id':'native','historyLoaded':False,'messages':[],'workspaceId':'project','historyReadOnlyReason':'Saved worker'}]}
    result=read_state(state,{})
    assert result['conversations'][0]['messageCount'] is None
    assert result['session']['historyReadOnlyReason']=='Saved worker'
    assert result['sharedHistory']['sessionCount']==1
    assert 'session.history' in result['_stateAccess']['history']


def test_conversation_overview_hides_subagents_but_exposes_parent_drilldown():
    state={'revision':1,'selectedSessionId':'root','sessions':[
        {'id':'worker','sessionKind':'worker','parentId':'root'},
        {'id':'root','sessionKind':'root'},
        {'id':'fork','sessionKind':'root','parentId':'root','nativeParentId':'root'},
        {'id':'legacy-fork','parentId':'root','forkTranscript':{'sourceSessionId':'root'}},
    ]}
    result=read_state(state,{})
    assert [row['id'] for row in result['conversations']]==['root','fork','legacy-fork']
    assert result['conversations'][0]['$statePath']=='/sessions/1'
    assert result['subagentChats']=={'total':1,'items':[{'id':'worker','title':None,'$statePath':'/sessions/0'}]}
    assert read_state(state,{'path':'/sessions/0'})['items']


# These fixtures never mount Team Pulse or contact a provider. The synthetic
# runtime keeps the real RuntimeControls -> Management -> AppService -> mounted
# app_control read path, rather than substituting a redaction implementation.
def _redaction_plan():
    return {
        'session': {'orchestrator': {'module': 'loop-live'},
                    'context': {'module': 'context-simple'}},
        'providers': [{'module': 'provider-fixture', 'config': {
            'api_key': 'SYNTHETIC_PROVIDER_CREDENTIAL', 'model': 'fixture-model'}}],
        'tools': [
            {'module': 'tool-team-pulse', 'config': {
                'key': 'SYNTHETIC_TEAM_PULSE_CREDENTIAL',
                'url': 'https://pulse.example.invalid'}},
            {'module': 'tool-keymap-fixture', 'config': {
                'key': 'F6', 'working_dir': '/workspace/fixture', 'read_only': True,
                'endpoint_reference': '${FIXTURE_ENDPOINT}'}},
        ],
        'hooks': [],
        'agents': {'worker/~alias': {'tools': [
            {'module': 'tool-team-pulse', 'config': {
                'key': 'SYNTHETIC_NESTED_CREDENTIAL'}}]}},
    }


def _assert_no_synthetic_credentials(value):
    body = json.dumps(value, ensure_ascii=False)
    for sentinel in ('SYNTHETIC_TEAM_PULSE_CREDENTIAL',
                     'SYNTHETIC_PROVIDER_CREDENTIAL',
                     'SYNTHETIC_NESTED_CREDENTIAL'):
        assert sentinel not in body


async def _configuration_tool(app, sid, native_role=None):
    from test_app_control_validation import mounted_tool

    async def bridge(operation, args):
        if native_role is not None:
            expected = sid if native_role == 'root' else 'native-child'
            assert args['_runtimeSessionId'] == expected
            assert ('_generationId' in args) == (native_role == 'root')
        return await app.app_bridge(operation, args, sid)

    if native_role is None:
        return await mounted_tool(bridge)
    from types import SimpleNamespace
    from amplifier_web.runtime_worker import Worker
    def native_coordinator(session_id):
        capabilities = {}
        return SimpleNamespace(session_id=session_id, get_capability=capabilities.get,
                               register_capability=capabilities.__setitem__)
    # Keep constructor-owned execution/activation defaults. Only the synthetic
    # coordinator, generation and host bridge below replace runtime boundaries.
    worker = Worker()
    root = native_coordinator(sid)
    worker.session = SimpleNamespace(coordinator=root)
    worker.runtime = SimpleNamespace(generation={'id': 'synthetic-generation', 'input_ids': []})
    worker.context_inputs, worker.context_bindings = [], {}
    worker.bridge = bridge
    coordinator = root if native_role == 'root' else native_coordinator('native-child')
    return await mounted_tool(worker.app_access_bridge(coordinator))


async def _tool_read(tool, args):
    result = await tool.execute({'operation': 'get_state', 'args': args})
    assert result.success, result.error
    return result.output


async def _configuration_pages(tool, path, *, limit=1):
    """Consume this finite fixture with checked progress, never an endless loop."""
    args = {'path': path, 'offset': 0, 'limit': limit}
    pages = []
    total = None
    for _ in range(256):
        page = await _tool_read(tool, args)
        assert page['path'] == path and page['offset'] == args['offset']
        assert page['type'] in {'array', 'object'}
        if total is None:
            total = page['total']
        assert page['total'] == total
        pages.append(page)
        next_offset = page['nextOffset']
        if next_offset is None:
            assert sum(len(item['items']) for item in pages) == total
            return pages
        assert type(next_offset) is int
        assert args['offset'] < next_offset < total
        assert next_offset == args['offset'] + len(page['items'])
        args.update(offset=next_offset, revision=page['revision'])
        if 'directoryRevision' in page:
            args['directoryRevision'] = page['directoryRevision']
    pytest.fail('Synthetic configuration paging exceeded 256 pages')


def _page_row(pages, key):
    rows = [row for page in pages for row in page['items'] if row['key'] == key]
    assert len(rows) == 1, f'Expected one public entry for {key!r}'
    row = rows[0]
    assert row['value'] is not None
    if isinstance(row['value'], dict) and '$statePath' in row['value']:
        assert row['value']['$statePath'] == row['path']
    return row


@pytest.fixture(params=['root', 'child'])
async def inspected_configuration(tmp_path, monkeypatch, request):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from amplifier_web.management import Management
    from amplifier_web.runtime_controls import RuntimeControls
    from test_configuration_authority import finished

    monkeypatch.setenv('AMPLIFIER_UNIFIED_IMPORT_HOME', str(tmp_path / 'no-import'))
    plan = _redaction_plan()
    original = copy.deepcopy(plan)
    provenance_rows = [copy.deepcopy(plan['tools'][0]), {
        'module': 'tool-padding-fixture',
        'description': 'Synthetic nonsecret provenance. ' * 1000}]
    capabilities = {'web.configurator': SimpleNamespace(
        snapshot=lambda: {}, tools_list=lambda: copy.deepcopy(provenance_rows))}
    coordinator = SimpleNamespace(
        config=plan, session_state={},
        get=lambda name: None,
        get_capability=capabilities.get,
        register_capability=capabilities.__setitem__)
    runtime = SimpleNamespace(start=AsyncMock(), close=AsyncMock())
    app = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    controls = None
    produced = []
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        controls = RuntimeControls(
            SimpleNamespace(session_id=sid, coordinator=coordinator),
            SimpleNamespace(generation=None, queued_inputs=0))

        async def control(target, operation, args):
            assert target == sid
            assert operation == 'configuration.inspect'
            value = await controls.perform(operation, args)
            produced.append(copy.deepcopy(value))
            return value

        runtime.control = control
        app.management = Management(app)
        tool = await _configuration_tool(app, sid, 'root')
        result = await tool.execute({'operation': 'dispatch', 'args': {
            'action': 'configuration.inspect', 'args': {'id': sid},
            'id': 'synthetic-configuration-inspect'}})
        assert result.success, result.error
        assert result.output['accepted']
        receipt = await finished(app, 'synthetic-configuration-inspect')
        assert receipt['phase'] == 'ready', receipt
        assert '$resource' in app._session(sid)['configuration']['provenance']
        tool = await _configuration_tool(app, sid, request.param)
        overview = await _tool_read(tool, {})
        yield app, tool, sid, overview, result.output, produced
        # Public projection must not replace mounted internal values.
        assert coordinator.config == original
    finally:
        try:
            if controls is not None:
                await controls.close()
        finally:
            await app.close()


@pytest.mark.parametrize('suffix', [
    '',
    '/configuration',
    '/configuration/plan',
    '/configuration/plan/tools',
    '/configuration/plan/tools/0',
    '/configuration/plan/tools/0/config',
    '/configuration/plan/tools/0/config/key',
    '/configuration/modules',
    '/configuration/modules/1/config',
    '/configuration/modules/1/config/key',
    '/configuration/plan/agents/worker~1~0alias/tools/0/config/key',
    '/configuration/plan/providers/0/config/api_key',
    '/configuration/provenance/tools/0/config/key',
])
async def test_configuration_inspect_public_paths_redact_credentials(
        inspected_configuration, suffix):
    app, tool, sid, overview, receipt, produced = inspected_configuration
    base = overview['session']['$statePath']
    args = {'path': base + suffix, 'revision': overview['revision'],
            'directoryRevision': overview['_stateAccess']['directoryRevision']}
    page = await _tool_read(tool, args)
    _assert_no_synthetic_credentials(page)
    if suffix.endswith(('/key', '/api_key')):
        assert page['value'] == '[REDACTED]'
    else:
        key, tail = {
            '': ('configuration', '/plan/tools/0/config/key'),
            '/configuration': ('plan', '/tools/0/config/key'),
            '/configuration/plan': ('tools', '/0/config/key'),
            '/configuration/plan/tools': (0, '/config/key'),
            '/configuration/plan/tools/0': ('config', '/key'),
            '/configuration/plan/tools/0/config': ('key', ''),
            '/configuration/modules': (1, '/config/key'),
            '/configuration/modules/1/config': ('key', ''),
        }[suffix]
        pages = ([page] if page['nextOffset'] is None else
                 await _configuration_pages(tool, args['path'], limit=50))
        _assert_no_synthetic_credentials(pages)
        row = _page_row(pages, key)
        followed = await _tool_read(tool, {**args, 'path': row['path'] + tail})
        assert followed['value'] == '[REDACTED]'


async def test_configuration_parent_pages_and_receipts_never_reveal_credentials(
        inspected_configuration):
    app, tool, sid, overview, receipt, produced = inspected_configuration
    _assert_no_synthetic_credentials(receipt)
    _assert_no_synthetic_credentials(overview)
    assert overview['session']['id'] == sid
    root_pages = await _configuration_pages(tool, '')
    sessions_ref = _page_row(root_pages, 'sessions')
    assert sessions_ref['value']['$statePath'] == '/sessions'
    session_pages = await _configuration_pages(tool, sessions_ref['value']['$statePath'])
    base = overview['session']['$statePath']
    owner = [row for page in session_pages for row in page['items'] if row['path'] == base]
    assert len(owner) == 1
    assert (await _tool_read(tool, {'path': owner[0]['path'] + '/id'}))['value'] == sid
    owner_pages = await _configuration_pages(tool, owner[0]['path'])
    configuration = _page_row(owner_pages, 'configuration')
    config_pages = await _configuration_pages(tool, configuration['path'])
    plan = _page_row(config_pages, 'plan')
    leaf = await _tool_read(tool, {'path': plan['path'] + '/tools/0/config/key'})
    for pages in (root_pages, session_pages, owner_pages, config_pages):
        _assert_no_synthetic_credentials(pages)
    assert leaf['value'] == '[REDACTED]'
    # Normalization deliberately replaces this duplicate with a pointer, not
    # another copy of the plan. Its collapsed form is an intended safe result.
    alias = await _tool_read(tool, {
        'path': f'/runtimeControl/{sid}/configuration.inspect'})
    _assert_no_synthetic_credentials(alias)
    assert {row['key']: row['value'] for row in alias['items']} == {
        'configurationSessionId': sid}


async def test_configuration_nonsecret_diagnostics_remain_readable(
        inspected_configuration):
    app, tool, sid, overview, receipt, produced = inspected_configuration
    base = overview['session']['$statePath'] + '/configuration/plan/tools'
    expected = {
        '/0/config/url': 'https://pulse.example.invalid',
        '/1/config/key': 'F6',
        '/1/config/working_dir': '/workspace/fixture',
        '/1/config/read_only': True,
        '/1/config/endpoint_reference': '${FIXTURE_ENDPOINT}',
    }
    for suffix, value in expected.items():
        assert (await _tool_read(tool, {'path': base + suffix}))['value'] == value


async def test_configuration_leaf_paging_cannot_reassemble_a_credential(
        inspected_configuration):
    app, tool, sid, overview, receipt, produced = inspected_configuration
    base = overview['session']['$statePath'] + '/configuration'
    for suffix in ('/plan/tools/0/config/key', '/modules/1/config/key'):
        args = {'path': base + suffix, 'offset': 0, 'limit': 3}
        pieces = []
        total = None
        for _ in range(64):
            page = await _tool_read(tool, args)
            assert page['path'] == args['path'] and page['offset'] == args['offset']
            assert page['type'] == 'string'
            if total is None:
                total = page['total']
            assert page['total'] == total
            pieces.append(page['value'])
            if page['nextOffset'] is None:
                assert sum(map(len, pieces)) == total
                break
            assert type(page['nextOffset']) is int
            assert args['offset'] < page['nextOffset'] < total
            assert page['nextOffset'] == args['offset'] + len(page['value'])
            args.update(offset=page['nextOffset'], revision=page['revision'],
                        directoryRevision=page['directoryRevision'])
        else:
            pytest.fail('Synthetic credential leaf exceeded 64 pages')
        assert ''.join(pieces) == '[REDACTED]'


@pytest.mark.parametrize('alias', ['/session', '/currentSession'])
async def test_configuration_unimplemented_aliases_do_not_expose_values(
        inspected_configuration, alias):
    app, tool, sid, overview, receipt, produced = inspected_configuration
    result = await tool.execute({'operation': 'get_state', 'args': {
        'path': alias + '/configuration/plan/tools/0/config/key'}})
    assert not result.success
    assert 'does not exist' in result.error['message']
    _assert_no_synthetic_credentials(result.error)


async def test_configuration_pointer_revision_guards_are_preserved(
        inspected_configuration):
    app, tool, sid, overview, receipt, produced = inspected_configuration
    base = overview['session']['$statePath'] + '/configuration'
    current = {'revision': overview['revision'],
               'directoryRevision': overview['_stateAccess']['directoryRevision']}
    positive = await _tool_read(tool, {'path': base, **current})
    assert _page_row([positive], 'plan')['path'] == base + '/plan'
    for revision_fields, diagnostic in (
            ({**current, 'revision': current['revision'] - 1},
             'App state changed. Read the current revision before continuing pages.'),
            ({'revision': current['revision']},
             'Include directoryRevision with revision when following saved chat indices. Read the current overview first.'),
            ({**current, 'directoryRevision': 'stale-directory'},
             'Saved chat directory changed. Read the current directory revision before continuing pages.')):
        result = await tool.execute({'operation': 'get_state', 'args': {
            'path': base, **revision_fields}})
        assert not result.success
        assert result.error['message'] == diagnostic
        _assert_no_synthetic_credentials(result.error)


async def test_configuration_producer_redacts_before_service_projection(
        inspected_configuration):
    app, tool, sid, overview, receipt, produced = inspected_configuration
    assert len(produced) == 1
    _assert_no_synthetic_credentials(produced[0])
    assert produced[0]['plan']['tools'][0]['config']['key'] == '[REDACTED]'
    assert produced[0]['modules'][1]['config']['key'] == '[REDACTED]'
    assert produced[0]['provenance']['tools'][0]['config']['key'] == '[REDACTED]'


@pytest.mark.parametrize('suffix', [
    '/configuration',
    '/configuration/plan/tools/0/config/key',
    '/configuration/modules/0/config/key',
    '/configuration/plan/agents/worker~1~0alias/tools/0/config/key',
    '/configuration/provenance/tools/0/config/key',
])
@pytest.mark.parametrize('native_role', ['root', 'child'])
async def test_legacy_configuration_read_projection_masks_without_rewriting_storage(
        tmp_path, monkeypatch, suffix, native_role):
    """Synthetic old saved state must be safe even without a new inspection."""
    monkeypatch.setenv('AMPLIFIER_UNIFIED_IMPORT_HOME', str(tmp_path / 'no-import'))
    plan = _redaction_plan()
    raw = {'plan': plan, 'modules': [
        {'section': 'tools', 'module': 'tool-team-pulse', 'id': 'tool-team-pulse',
         'config': copy.deepcopy(plan['tools'][0]['config'])}],
        'provenance': {'tools': [copy.deepcopy(plan['tools'][0])],
                       'padding': 'Synthetic nonsecret provenance. ' * 1000}}
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        app._session()['configuration'] = copy.deepcopy(raw)
        app._session()['structuredData'] = {'key': 'ordinary user data'}
        # Leave another owner selected so the source owner is dormant on reopen.
        await app.dispatch('session.create', {})
        app._save()
    finally:
        await app.close()
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        from amplifier_web.state_records import load_owner
        from amplifier_web.session_projection import hydrate
        resident_before = [row['id'] for row in app._state['sessions']]
        selected_before = app._state['selectedSessionId']
        assert sid not in resident_before
        durable_before = copy.deepcopy(load_owner(app.db, sid))
        detached = {'sessions': [copy.deepcopy(durable_before[0])]}
        hydrate(app.data_dir, detached, app.db)
        stored_before = copy.deepcopy(detached['sessions'][0]['configuration'])
        assert stored_before['plan']['tools'][0]['config']['key'] == 'SYNTHETIC_TEAM_PULSE_CREDENTIAL'
        provenance_ref = copy.deepcopy(stored_before['provenance'])
        provenance_before = copy.deepcopy(app.state_resource(provenance_ref['$resource']))
        tool = await _configuration_tool(app, sid, native_role)
        overview = await _tool_read(tool, {})
        page = await _tool_read(tool, {
            'path': overview['session']['$statePath'] + suffix,
            'revision': overview['revision'],
            'directoryRevision': overview['_stateAccess']['directoryRevision']})
        _assert_no_synthetic_credentials(page)
        if suffix.endswith('/key'):
            assert page['value'] == '[REDACTED]'
        else:
            plan = _page_row([page], 'plan')
            assert (await _tool_read(tool, {
                'path': plan['path'] + '/tools/0/config/key'}))['value'] == '[REDACTED]'
        assert (await _tool_read(tool, {
            'path': overview['session']['$statePath'] + '/structuredData/key'}
        ))['value'] == 'ordinary user data'
        # Reading a public projection is not a credential-storage migration.
        assert [row['id'] for row in app._state['sessions']] == resident_before
        assert app._state['selectedSessionId'] == selected_before
        assert load_owner(app.db, sid) == durable_before
        detached_after = {'sessions': [copy.deepcopy(load_owner(app.db, sid)[0])]}
        hydrate(app.data_dir, detached_after, app.db)
        stored_after = detached_after['sessions'][0]['configuration']
        assert stored_after == stored_before
        assert stored_after['plan'] == raw['plan']
        assert stored_after['modules'] == raw['modules']
        assert stored_after['provenance'] == provenance_ref
        assert app.state_resource(provenance_ref['$resource']) == provenance_before == raw['provenance']
    finally:
        await app.close()


@pytest.mark.parametrize('native_role', ['root', 'child'])
@pytest.mark.parametrize('shape', ['direct-inspection', 'wrapped-configuration'])
@pytest.mark.parametrize('surface', ['parent', 'leaf'])
async def test_nonresident_raw_runtime_configuration_read_is_redacted_without_rewrite(
        tmp_path, monkeypatch, native_role, shape, surface):
    """Seed only the legacy durable runtime seam, bypassing fixture normalization."""
    from amplifier_web import state_records
    monkeypatch.setenv('AMPLIFIER_UNIFIED_IMPORT_HOME', str(tmp_path / 'no-import'))
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        target = app._session()['id']
        await app.dispatch('session.create', {})
        reader = app._session()['id']
        app._save()
    finally:
        await app.close()
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        raw = {'plan': _redaction_plan(), 'modules': [], 'provenance': {}}
        operation = 'configuration.inspect' if shape == 'direct-inspection' else 'configuration.apply'
        value = raw if shape == 'direct-inspection' else {
            'configuration': raw, 'requiresRestart': True}
        record = {operation: value}
        # This is a synthetic legacy store record, not a runtime inspection
        # result. Saving through Management would normalize away the seam.
        encoded = json.dumps(record, ensure_ascii=False, separators=(',', ':'))
        app.db.execute(state_records.UPSERT, ('runtime', target, encoded))
        app.db.commit()
        resident_before = [row['id'] for row in app._state['sessions']]
        controls_before = list(app._state.get('runtimeControl', {}))
        assert target not in resident_before and target not in controls_before
        assert app._state['selectedSessionId'] == reader
        query = "SELECT kind,CAST(value AS BLOB) FROM state_records WHERE id=? ORDER BY kind"
        durable_before = app.db.execute(query, (target,)).fetchall()
        assert dict(durable_before)['runtime'] == encoded.encode()
        assert b'SYNTHETIC_TEAM_PULSE_CREDENTIAL' in dict(durable_before)['runtime']
        assert 'configuration' not in state_records.load_owner(app.db, target)[0]
        loaded = []
        original_load = state_records.load_owner

        def observe_load(db, identity):
            loaded.append(identity)
            return original_load(db, identity)

        monkeypatch.setattr(state_records, 'load_owner', observe_load)
        tool = await _configuration_tool(app, reader, native_role)
        collection = await _configuration_pages(tool, '/runtimeControl')
        owner = _page_row(collection, target)
        owner_pages = await _configuration_pages(tool, owner['path'])
        control = _page_row(owner_pages, operation)
        config_path = control['path']
        if shape == 'wrapped-configuration':
            wrapper_pages = await _configuration_pages(tool, config_path)
            config_path = _page_row(wrapper_pages, 'configuration')['path']
        else:
            wrapper_pages = []
        path = config_path + ('/plan/tools/0/config/key' if surface == 'leaf' else '')
        page = await _tool_read(tool, {'path': path})
        assert target in loaded  # directory_state._runtime loaded durable JSON
        assert [row['id'] for row in app._state['sessions']] == resident_before
        assert list(app._state.get('runtimeControl', {})) == controls_before
        assert app._state['selectedSessionId'] == reader
        assert app.db.execute(query, (target,)).fetchall() == durable_before
        _assert_no_synthetic_credentials([collection, owner_pages, wrapper_pages, page])
        if surface == 'parent':
            plan = _page_row([page], 'plan')
            page = await _tool_read(tool, {'path': plan['path'] + '/tools/0/config/key'})
        assert page['value'] == '[REDACTED]'
        assert app.db.execute(query, (target,)).fetchall() == durable_before
    finally:
        await app.close()


async def test_legacy_session_configuration_normalizes_to_canonical_owner(
        tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_UNIFIED_IMPORT_HOME', str(tmp_path / 'no-import'))
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        raw = {'plan': _redaction_plan(), 'modules': [], 'provenance': {}}
        assert 'configuration' not in app._session()
        app.state['sessionConfiguration'] = {sid: copy.deepcopy(raw)}
        app._save()
        assert 'sessionConfiguration' not in app.state
        assert app._session()['configuration'] == raw
        assert raw['plan']['tools'][0]['config']['key'] == 'SYNTHETIC_TEAM_PULSE_CREDENTIAL'
        tool = await _configuration_tool(app, sid, 'root')
        overview = await _tool_read(tool, {})
        owner_pages = await _configuration_pages(tool, overview['session']['$statePath'])
        configuration = _page_row(owner_pages, 'configuration')
        page = await _tool_read(tool, {
            'path': configuration['path'] + '/plan/tools/0/config/key'})
        assert app._session()['configuration'] == raw
        assert page['value'] == '[REDACTED]'
        # The obsolete global alias need not survive normalization.
        absent = await tool.execute({'operation': 'get_state', 'args': {
            'path': f'/sessionConfiguration/{sid}'}})
        assert not absent.success and 'does not exist' in absent.error['message']
    finally:
        await app.close()


@pytest.mark.parametrize('native_role', ['root', 'child'])
async def test_configuration_resource_fragment_preserves_module_context(tmp_path, native_role):
    from amplifier_web.resource_files import put
    from amplifier_web import cold_display
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        original = {'key': 'SYNTHETIC_TEAM_PULSE_CREDENTIAL', 'url': 'https://pulse.example.invalid'}
        ref = put(app.db, original)
        plan = {'tools': [{'module': 'tool-team-pulse', 'config': ref}]}
        app._session()['configuration'] = {'plan': plan}
        tool = await _configuration_tool(app, sid, native_role)
        overview = await _tool_read(tool, {})
        path = overview['session']['$statePath'] + '/configuration/plan/tools/0/config'
        assert (await _tool_read(tool, {'path': path + '/key'}))['value'] == '[REDACTED]'
        assert (await _tool_read(tool, {'path': path + '/url'}))['value'] == original['url']
        assert app._session()['configuration']['plan'] == plan
        assert app.state_resource(ref['$resource']) == original
        # Internal cold manifests must not provide an unprojected resource alias.
        saved = {'configuration': {'plan': _redaction_plan()}}
        frozen = put(app.db, saved['configuration'])
        row = cold_display.ColdRecord({'id': sid, cold_display.MARKER: {'configuration': frozen}}, app.db)
        state = {'revision': 1, 'sessions': [row]}
        for suffix in ('', '/configuration', '/configuration/plan/tools/0/config/key'):
            with pytest.raises(ValueError, match='does not exist'):
                read_state(state, {'path': '/sessions/0/_coldFields' + suffix}, session_id=sid,
                           resolve=app.state_resource)
        assert dict.__contains__(row, cold_display.MARKER)
        assert not dict.__contains__(row, 'configuration')
    finally:
        await app.close()


def test_configuration_projection_is_lazy_and_does_not_sanitize_arbitrary_user_json(monkeypatch):
    from amplifier_web import cold_display
    raw = {'tools': [{'module': 'tool-team-pulse', 'config': {'key': 'ordinary example'}}],
           'api_key': 'user-authored example', 'field_type': 'secret', 'default': 'user text'}
    row = cold_display.ColdRecord({'id': 'root', 'title': 'Visible',
        'structuredData': copy.deepcopy(raw),
        cold_display.MARKER: {'configuration': {'$resource': 'unused'}}}, None)
    state = {'revision': 1, 'sessions': [row], 'runtimeControl': {}}
    def unexpected(*args):
        pytest.fail('Unrelated read must not load cold configuration/resources')
    monkeypatch.setattr(cold_display, 'load', unexpected)
    assert read_state(state, {'path': '/revision'}, session_id='root', resolve=unexpected)['value'] == 1
    assert read_state(state, {'path': '/sessions/0/title'}, session_id='root', resolve=unexpected)['value'] == 'Visible'
    page = read_state(state, {'path': '/sessions/0', 'limit': 1}, session_id='root', resolve=unexpected)
    assert page['items'][0]['value'] == 'root'
    page = read_state(state, {'path': '/sessions'}, session_id='root', resolve=unexpected)
    public = page['items'][0]['value']
    assert public['configuration'] == {'$statePath': '/sessions/0/configuration'}
    assert public['structuredData'] == raw
    assert not dict.__contains__(row, 'configuration')
    # The generic resource pager is not an app-state or credential-schema API.
    generic = {'sessions': [{'configuration': raw}]}
    assert read_state(generic, {'path': '/sessions/0/configuration/api_key'})['value'] == raw['api_key']
