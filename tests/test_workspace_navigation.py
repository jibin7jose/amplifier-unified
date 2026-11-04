import copy
from pathlib import Path

import pytest

from amplifier_web.service import AppError, AppService
from amplifier_web.workspace_navigation import snapshot, view_patch, workspace_pins, pin_order


def workspace(identity, path, **extra):
    return {'id': identity, 'path': path, 'name': path.rsplit('/', 1)[-1] if path else identity,
            'available': True, **extra}


def chat(identity, workspace_id, **extra):
    return {'id': identity, 'workspaceId': workspace_id, 'sessionKind': 'root', **extra}


def state(workspaces, sessions, selected=None):
    return {'workspaces': workspaces, 'sessions': sessions, 'selectedWorkspaceId': selected, 'view': {}}


def browse(value, **patch):
    value['view'].update(view_patch(value, patch))
    return snapshot(value)


def test_folder_index_shows_only_paths_to_roots_and_separates_selection_from_browsing():
    value = state([
        workspace('app', '/home/me/dev/app'),
        workspace('nested', '/home/me/dev/app/tools/nested'),
        workspace('other', '/home/me/play/other'),
    ], [chat('a', 'app'), chat('n', 'nested'), chat('o', 'other')], 'app')
    initial = snapshot(value)
    assert initial['rootPath'] == '/home/me'
    assert initial['path'] == '/home/me/dev'
    assert initial['parentPath'] == '/home/me'
    assert initial['rows'] == [{'path': '/home/me/dev/app', 'parentPath': '/home/me/dev', 'name': 'app', 'workspaceId': 'app',
                                'chatCount': 1, 'descendantWorkspaceCount': 1, 'canBrowse': True, 'unread': 0,
                                'recentActivityAt': 0, 'activityCounts': {'attention': 0, 'working': 0, 'unread': 0, 'idle': 1}, 'pathLabel': 'app'}]
    inside = browse(value, navWorkspacePath='/home/me/dev/app')
    assert value['selectedWorkspaceId'] == 'app'
    assert inside['rows'][0]['workspaceId'] is None
    assert inside['rows'][0]['path'] == '/home/me/dev/app/tools'
    assert inside['rows'][0]['canBrowse']
    assert inside['rows'][0]['descendantWorkspaceCount'] == 1
    assert inside['rows'][0]['chatCount'] == 0
    leaf = browse(value, navWorkspacePath='/home/me/dev/app/tools')['rows'][0]
    assert leaf['workspaceId'] == 'nested'
    assert not leaf['canBrowse']
    with pytest.raises(ValueError, match='no deeper workspaces'):
        browse(value, navWorkspacePath=leaf['path'])
    assert [row['path'] for row in inside['breadcrumbs']] == ['/home/me', '/home/me/dev', '/home/me/dev/app']


def test_available_empty_folders_remain_visible_without_counting_workers_or_mutating_history():
    value = state([
        workspace('valid', '/dev/app'), workspace('prefix', '/dev/application'),
        workspace('gone', '/deleted/gone', available=False),
        workspace('unknown', None), workspace('empty', '/empty'),
        workspace('worker-only', '/workers'), workspace('unchecked', '/unchecked', available=None),
    ], [chat('valid-chat', 'valid'), chat('prefix-chat', 'prefix'), chat('gone-chat', 'gone'),
        chat('unresolved-chat', 'unknown'), chat('worker', 'worker-only', sessionKind='worker'),
        chat('internal', 'worker-only', sessionKind='internal'),
        chat('unchecked-chat', 'unchecked')], 'valid')
    original = copy.deepcopy(value)
    result = snapshot(value)
    assert result['totalWorkspaces'] == 4
    assert [row['path'] for row in result['rows']] == ['/dev/app', '/dev/application']
    assert all(not row['canBrowse'] for row in result['rows'])
    assert value == original
    recent = browse(value, navWorkspaceMode='recent')
    assert {row['workspaceId']: row['chatCount'] for row in recent['rows']} == {
        'valid': 1, 'prefix': 1, 'empty': 0, 'worker-only': 0}


def test_legacy_paths_forks_and_unread_counts_are_scoped_to_top_level_chats_once():
    value = state([
        workspace('outer', '/work/app'), workspace('inner', '/work/app/child'),
        workspace('boundary', '/work/application'),
    ], [chat('first', 'outer'), {'id': 'fork', 'workspace': '/work/app', 'parentId': 'first'},
        chat('second', 'inner'), chat('second', 'inner'), chat('worker', 'inner', sessionKind='worker'),
        chat('boundary-chat', 'boundary')], 'outer')
    value['attention'] = {'sessions': {'first': 1, 'fork': 1, 'second': 1, 'worker': 1, 'boundary-chat': 1}}
    result = snapshot(value)
    outer, boundary = result['rows']
    assert outer['chatCount'] == 2
    assert outer['descendantWorkspaceCount'] == 1
    assert outer['unread'] == 3
    assert boundary['unread'] == 1 and boundary['descendantWorkspaceCount'] == 0
    nested = browse(value, navWorkspacePath=outer['path'])['rows'][0]
    assert nested['chatCount'] == 1 and nested['unread'] == 1


@pytest.mark.parametrize('workspace_archive', ['active', 'archived', 'all'])
def test_workspace_summary_excludes_archived_chats_until_restored(workspace_archive):
    value = state([workspace('app', '/work/app', archived=workspace_archive == 'archived')],
                  [chat('visible', 'app', recentActivityAt=10),
                   chat('archived', 'app', status='working', recentActivityAt=20)], 'app')
    value['view'].update(navWorkspaceMode='recent', navWorkspaceArchive=workspace_archive,
                         navArchive='all')
    value['conversationOrganization'] = {'archived': {'archived': 1}}
    value['attention'] = {'sessions': {'archived': 1}}
    row = snapshot(value)['rows'][0]
    assert row['chatCount'] == 1
    assert row['activityCounts'] == {'attention': 0, 'working': 0, 'unread': 0, 'idle': 1}
    assert row['unread'] == 0 and row['recentActivityAt'] == 10
    assert value['sessions'][1]['status'] == 'working'
    value['conversationOrganization']['archived'].clear()
    row = snapshot(value)['rows'][0]
    assert row['chatCount'] == 2 and row['activityCounts']['working'] == 1
    assert row['unread'] == 1 and row['recentActivityAt'] == 20


def test_search_uses_full_paths_aliases_and_fnmatch_for_duplicate_folder_names():
    value = state([
        workspace('one', '/work/team-one/app', name='Design kit'),
        workspace('two', '/work/team-two/app'),
        workspace('nested', '/work/team-two/app/demo'),
    ], [chat('a', 'one'), chat('b', 'two'), chat('c', 'nested')], 'one')
    plain = browse(value, navWorkspaceFilter='APP')
    assert len(plain['rows']) == 3
    assert plain['rows'][0]['customName'] == 'Design kit'
    alias = browse(value, navWorkspaceFilter='design')
    assert [row['workspaceId'] for row in alias['rows']] == ['one']
    glob = browse(value, navWorkspaceFilter='*/TEAM-?WO/APP')
    assert [row['workspaceId'] for row in glob['rows']] == ['two']
    assert glob['rows'][0]['canBrowse']
    browse(value, navWorkspacePath='/work/team-two/app')
    assert snapshot(value)['filter'] == ''
    assert snapshot(value)['rows'][0]['workspaceId'] == 'nested'


def test_pages_are_bounded_and_reset_after_browsing_or_filtering():
    value = state([workspace(str(i), f'/projects/project-{i:03}') for i in range(205)],
                  [chat(f'chat-{i}', str(i)) for i in range(205)])
    assert snapshot(value)['totalWorkspaces'] == 205
    assert len(snapshot(value)['rows']) == 100
    second = browse(value, navWorkspacePage=2)
    assert second['page'] == 2 and second['pages'] == 3
    assert (second['start'], second['end']) == (100, 200)
    assert second['rows'][0]['name'] == 'project-100'
    assert len(browse(value, navWorkspacePage=999)['rows']) == 5
    assert snapshot(value)['page'] == 3
    assert (snapshot(value)['start'], snapshot(value)['end']) == (200, 205)
    filtered = browse(value, navWorkspaceFilter='*-00?')
    assert filtered['page'] == 1 and filtered['totalRows'] == 10
    reset = browse(value, navWorkspacePath='/projects')
    assert reset['page'] == 1 and reset['filter'] == ''


def test_browsing_persists_for_same_selection_but_reveals_new_workspace_on_selection():
    value = state([workspace('one', '/home/dev/one'), workspace('two', '/home/play/two')],
                  [chat('a', 'one'), chat('b', 'two')], 'one')
    browse(value, navWorkspacePath='/home/play', navWorkspaceFilter='two')
    value['view'].update(view_patch(value, {'draft': 'unchanged location'}))
    assert snapshot(value)['path'] == '/home/play' and snapshot(value)['filter'] == 'two'
    value['selectedWorkspaceId'] = 'two'
    result = snapshot(value)
    assert result['path'] == '/home/play' and result['filter'] == ''
    browse(value, navWorkspaceFilter='play')
    assert value['view']['navWorkspaceBrowseFor'] == 'two'
    assert value['view']['navWorkspacePath'] == '/home/play'


def test_stale_location_falls_back_to_nearest_remaining_ancestor():
    value = state([workspace('app', '/home/dev/app'), workspace('nested', '/home/dev/app/nested'),
                   workspace('other', '/home/play/other')],
                  [chat('a', 'app'), chat('n', 'nested'), chat('o', 'other')], 'app')
    browse(value, navWorkspacePath='/home/dev/app')
    value['workspaces'][1]['available'] = False
    result = snapshot(value)
    assert result['path'] == '/home/dev'
    assert result['rows'][0]['workspaceId'] == 'app'
    value['workspaces'][0]['available'] = False
    result = snapshot(value)
    assert result['rootPath'] == result['path'] == '/home/play'
    assert result['rows'][0]['workspaceId'] == 'other'


@pytest.mark.parametrize('patch', [
    {'navWorkspacePath': '/elsewhere'}, {'navWorkspacePath': '../work'}, {'navWorkspacePath': '/work/../work'},
    {'navWorkspacePath': '/work\0'}, {'navWorkspacePath': False}, {'navWorkspacePath': ''},
    {'navWorkspaceFilter': 123}, {'navWorkspaceFilter': 'a' * 501},
    {'navWorkspacePage': True}, {'navWorkspacePage': 0}, {'navWorkspacePage': 1.5}, {'navWorkspacePage': 1000001},
])
def test_browse_controls_reject_invalid_types_paths_and_bounds(patch):
    value = state([workspace('a', '/work/app')], [chat('one', 'a')])
    with pytest.raises(ValueError):
        view_patch(value, patch)


def test_windows_drive_and_path_boundaries_do_not_depend_on_host_platform():
    value = state([workspace('a', r'C:\Users\me\dev\app', name='app'),
                   workspace('nested', r'C:\Users\me\dev\app\child', name='child'),
                   workspace('b', r'D:\app', name='app')],
                  [chat('a', 'a'), chat('n', 'nested'), chat('b', 'b')], 'a')
    result = snapshot(value)
    assert result['rootPath'] == ''
    assert result['path'] == 'C:\\Users\\me\\dev'
    assert result['rows'][0]['workspaceId'] == 'a' and result['rows'][0]['canBrowse']
    root = browse(value, navWorkspacePath='')
    assert [row['path'] for row in root['rows']] == ['C:\\', 'D:\\']
    assert root['parentPath'] is None
    assert root['breadcrumbs'] == [{'path': '', 'name': 'Workspaces'}]
    drive = browse(value, navWorkspacePath='D:\\')
    assert drive['parentPath'] == ''
    assert not drive['rows'][0]['canBrowse']
    with pytest.raises(ValueError):
        view_patch(value, {'navWorkspacePath': 'C:dev'})


def test_filesystem_root_workspaces_remain_selectable_and_have_only_valid_descendants():
    value = state([workspace('root', '/', name='/'), workspace('dev', '/usr/dev')],
                  [chat('root-chat', 'root'), chat('dev-chat', 'dev')], 'root')
    assert snapshot(value)['rows'][0]['workspaceId'] == 'root'
    root = browse(value, navWorkspacePath='')
    assert root['rows'][0]['path'] == '/'
    assert root['rows'][0]['workspaceId'] == 'root'
    assert root['rows'][0]['canBrowse']
    inside = browse(value, navWorkspacePath='/')
    assert inside['parentPath'] == ''
    assert inside['rows'][0]['path'] == '/usr'
    only_root = state([workspace('root', '/', name='/')], [chat('root-chat', 'root')], 'root')
    assert snapshot(only_root)['rows'][0]['workspaceId'] == 'root'
    assert not snapshot(only_root)['rows'][0]['canBrowse']


def test_unc_shares_and_posix_folders_have_distinct_root_branches():
    value = state([workspace('one', r'\\server\one\app', name='app'),
                   workspace('two', r'\\server\two\app', name='app'),
                   workspace('posix', '/dev/app')],
                  [chat('one', 'one'), chat('two', 'two'), chat('posix', 'posix')])
    result = snapshot(value)
    assert result['rootPath'] == result['path'] == ''
    assert {row['path'] for row in result['rows']} == {'/', '\\\\server\\one\\', '\\\\server\\two\\'}
    assert all(row['workspaceId'] is None and row['descendantWorkspaceCount'] == 1 for row in result['rows'])
    share = browse(value, navWorkspacePath='\\\\server\\one\\')
    assert share['rows'][0]['workspaceId'] == 'one'
    assert not share['rows'][0]['canBrowse']


def test_empty_registry_and_selected_empty_workspace_do_not_require_filesystem_access(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError('The folder projection must not scan the filesystem')
    monkeypatch.setattr(Path, 'is_dir', forbidden)
    value = state([workspace('empty', '/projects/empty')], [], 'empty')
    result = snapshot(value)
    assert result['totalWorkspaces'] == 1
    assert [(row['workspaceId'], row['chatCount']) for row in result['rows']] == [('empty', 0)]
    assert result['page'] == result['pages'] == 1
    assert value['selectedWorkspaceId'] == 'empty'


async def test_agent_and_ui_share_durable_explorer_controls_and_selection_scope(tmp_path):
    first = tmp_path / 'dev' / 'first'
    second = tmp_path / 'other' / 'second'
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    app = AppService(tmp_path / 'state', workspace=first)
    await app.dispatch('session.create', {})
    first_chat = app.state['selectedSessionId']
    await app.dispatch('workspace.add', {'path': str(second)})
    await app.dispatch('session.create', {})
    second_chat = app.state['selectedSessionId']
    await app.dispatch('session.select', {'id': first_chat})
    await app.app_bridge('dispatch', {'action': 'view.update', 'args': {'patch': {'navWorkspacePath': str(tmp_path / 'other')}}}, first_chat)
    assert app.state['selectedSessionId'] == first_chat
    overview = await app.app_bridge('get_state', {}, first_chat)
    assert overview['workspaceExplorer']['path'] == str(tmp_path / 'other')
    assert overview['workspaceExplorer']['rows'][0]['path'] == str(second)
    assert 'navWorkspacePath' in overview['_stateAccess']['history']
    actions = await app.app_bridge('list_actions', {'prefix': 'view.'}, first_chat)
    assert 'navWorkspacePage' in actions['items'][0]['description']
    with pytest.raises(AppError):
        await app.dispatch('view.update', {'patch': {'navWorkspaceBrowseFor': 'spoof'}})
    with pytest.raises(AppError):
        await app.dispatch('view.update', {'patch': {'navWorkspacePage': False}})
    with pytest.raises(AppError):
        await app.dispatch('view.update', {'patch': {'navWorkspaceAncestorsOpen': 1}})
    await app.app_bridge('dispatch', {'action': 'view.update', 'args': {'patch': {'navWorkspaceAncestorsOpen': True}}}, first_chat)
    assert app.get_state()['view']['navWorkspaceAncestorsOpen'] is True
    await app.dispatch('view.update', {'patch': {'navPinned': True}})
    assert app.get_state()['workspaceExplorer']['path'] == str(tmp_path / 'other')
    await app.close()
    restored = AppService(tmp_path / 'state', workspace=first)
    assert restored.get_state()['workspaceExplorer']['path'] == str(tmp_path / 'other')
    await restored.dispatch('session.select', {'id': second_chat})
    assert restored.get_state()['workspaceExplorer']['path'] == str(second.parent)
    assert not restored.get_state()['view'].get('navWorkspaceAncestorsOpen')
    await restored.dispatch('session.select', {'id': first_chat})
    assert restored.get_state()['workspaceExplorer']['path'] == str(first.parent)
    await restored.close()


def test_workspace_pins_are_ordered_deduplicated_and_independent_of_chat_activity():
    value = state([workspace('empty', '/projects/empty'), workspace('old', '/projects/old'),
                   workspace('busy', '/projects/busy')], [chat('busy-chat', 'busy', recentActivityAt=999)], 'busy')
    value['pinnedWorkspaceIds'] = ['old', 'empty', 'old', 'unknown', None]
    value['pinnedSessionIds'] = ['busy-chat']
    original = copy.deepcopy(value)
    result = browse(copy.deepcopy(value), navWorkspaceMode='recent')
    assert [row['workspaceId'] for row in result['rows']] == ['busy', 'empty', 'old']
    assert [row.get('pinned', False) for row in result['rows']] == [False, True, True]
    assert result['rows'][1]['chatCount'] == 0
    assert workspace_pins(value) == ['old', 'empty']
    assert value == original
    assert pin_order(value, 'workspace.pin', {'id': 'old', 'pinned': True}) == ['old', 'empty']
    assert pin_order(value, 'workspace.pin', {'id': 'old', 'pinned': False}) == ['empty']
    assert pin_order(value, 'workspace.pinOrder', {'ids': ['empty', 'old']}) == ['empty', 'old']
    assert value == original


@pytest.mark.parametrize('action,args', [
    ('workspace.pin', {'id': 'unknown', 'pinned': True}),
    ('workspace.pin', {'id': 'empty', 'pinned': 1}),
    ('workspace.pinOrder', {'ids': ['empty', 'empty']}),
    ('workspace.pinOrder', {'ids': []}),
    ('workspace.pinOrder', {'ids': ['unknown']}),
    ('workspace.pinOrder', {'ids': [None]}),
])
def test_workspace_pin_changes_reject_invalid_ids_and_incomplete_orders(action, args):
    value = state([workspace('empty', '/projects/empty')], [])
    value['pinnedWorkspaceIds'] = ['empty']
    before = copy.deepcopy(value)
    with pytest.raises(ValueError):
        pin_order(value, action, args)
    assert value == before


def test_unavailable_pins_keep_identity_and_order_but_do_not_expose_missing_folders():
    value = state([workspace('one', '/projects/one'), workspace('two', '/projects/two'),
                   workspace('busy', '/projects/busy')], [chat('b', 'busy', recentActivityAt=999)])
    value['pinnedWorkspaceIds'] = ['two', 'one']
    value['view']['navWorkspaceMode'] = 'recent'
    value['workspaces'][1]['available'] = False
    assert [row['workspaceId'] for row in snapshot(value)['rows']] == ['busy', 'one']
    assert workspace_pins(value) == ['two', 'one']
    value['workspaces'][1].update(name='Renamed', available=True)
    result = snapshot(value)
    assert [row['workspaceId'] for row in result['rows']] == ['busy', 'one', 'two']
    assert result['rows'][2]['customName'] == 'Renamed'
    assert [row['workspaceId'] for row in snapshot(value, pinned_only=True)['rows']] == ['two', 'one']
    assert value['pinnedWorkspaceIds'] == ['two', 'one']


def test_same_path_registrations_use_pinned_identity_without_duplicate_rows():
    value = state([workspace('legacy', '/projects/app'), workspace('current', '/projects/app')], [], 'current')
    value['view']['navWorkspaceMode'] = 'recent'
    value['pinnedWorkspaceIds'] = ['legacy']
    result = snapshot(value)
    assert len(result['rows']) == 1
    assert result['rows'][0]['workspaceId'] == 'legacy'
    assert result['rows'][0]['pinned']
    assert result['selected']['workspaceId'] == 'current'


def test_workspace_cache_keys_cover_whole_pin_vector_including_unavailable_and_off_page():
    from amplifier_web.state_projections import StateProjections
    value = state([workspace(str(i), f'/projects/{i:03}') for i in range(115)], [])
    value['view']['navWorkspaceMode'] = 'recent'
    value['workspaces'][110]['available'] = False
    value['pinnedWorkspaceIds'] = ['110', '109', '108']
    projections = StateProjections()
    first = projections.workspaces(value)
    index = projections.values[('workspace-index', 'active')]
    key = projections.shell_key(value)
    chat_cache = object()
    projections.values[('chats', 'sentinel')] = chat_cache
    scope = projections.workspace_scope(value)
    value['pinnedWorkspaceIds'] = ['108', '110', '109']
    assert projections.workspace_scope(value) != scope
    projections.workspace_pins_changed()
    second = projections.workspaces(value)
    assert second is not first
    assert [row['workspaceId'] for row in second['rows']] == [row['workspaceId'] for row in first['rows']]
    assert projections.shell_key(value) != key
    assert projections.values[('workspace-index', 'active')] is index
    assert projections.values[('chats', 'sentinel')] is chat_cache
    key = projections.shell_key(value)
    projections.invalidate(state=value, session_ids=set())
    value['workspaces'][110]['available'] = True
    restored = projections.workspaces(value)
    assert [row['workspaceId'] for row in restored['rows'][:3]] == ['0', '1', '2']
    assert [row['workspaceId'] for row in snapshot(value, pinned_only=True)['rows']] == ['108', '110', '109']
    assert projections.shell_key(value) != key


async def test_workspace_pins_persist_in_records_checkpoints_backup_export_and_restart(tmp_path):
    import json
    import sqlite3
    from amplifier_web.state_records import load
    paths = [tmp_path / name for name in ('one', 'two')]
    for path in paths:
        path.mkdir()
    app = AppService(tmp_path / 'state', workspace=paths[0])
    await app.dispatch('workspace.add', {'path': str(paths[1])})
    one, two = [row['id'] for row in app.state['workspaces']]
    assert not app.state['sessions']
    args = {'id': one, 'pinned': True}
    receipt = await app.dispatch('workspace.pin', args, command_id='pin-empty')
    repeated = await app.dispatch('workspace.pin', args, command_id='pin-empty')
    assert repeated['duplicate'] and repeated['revision'] == receipt['revision']
    await app.dispatch('workspace.pin', args)
    await app.dispatch('workspace.pin', {'id': two, 'pinned': True})
    await app.dispatch('workspace.pinOrder', {'ids': [two, one]})
    assert load(app.db)['pinnedWorkspaceIds'] == [two, one]
    await app.dispatch('workspace.rename', {'id': one, 'name': 'Renamed one'})
    # An ordinary full checkpoint must retain the newly introduced preference.
    app._save()
    assert load(app.db)['pinnedWorkspaceIds'] == [two, one]
    exported = await app.dispatch('state.export', {})
    assert json.loads(exported['effects'][0]['content'])['pinnedWorkspaceIds'] == [two, one]
    backup = sqlite3.connect(tmp_path / 'backup.sqlite3')
    app.db.backup(backup)
    assert load(backup)['pinnedWorkspaceIds'] == [two, one]
    backup.close()
    await app.close()
    restored = AppService(tmp_path / 'state', workspace=paths[0])
    assert restored.state['pinnedWorkspaceIds'] == [two, one]
    recent = snapshot({**restored.state, 'view': {'navWorkspaceMode': 'recent'}})
    assert [row['workspaceId'] for row in recent['rows']] == [one, two]
    assert recent['rows'][0]['customName'] == 'Renamed one'
    assert [row['workspaceId'] for row in snapshot({**restored.state, 'view': {'navWorkspaceMode': 'recent'}}, pinned_only=True)['rows']] == [two, one]
    await restored.dispatch('workspace.remove', {'id': two})
    assert restored.state['pinnedWorkspaceIds'] == [one]
    assert paths[1].is_dir() and not restored.state['sessions']
    await restored.close()


async def test_workspace_pin_failed_transaction_rolls_back_preference_and_receipt(tmp_path, monkeypatch):
    import amplifier_web.state_records as records
    folder = tmp_path / 'workspace'; folder.mkdir()
    app = AppService(tmp_path / 'state', workspace=folder)
    before = copy.deepcopy(app._state)
    def fail(*_args, **_kwargs):
        raise RuntimeError('Synthetic record failure')
    with monkeypatch.context() as patch:
        patch.setattr(records, 'save', fail)
        with pytest.raises(RuntimeError, match='Synthetic record failure'):
            await app.dispatch('workspace.pin', {'id': app.state['selectedWorkspaceId'], 'pinned': True},
                               command_id='failed-pin')
    assert app._state == before
    assert not app.db.execute("SELECT 1 FROM commands WHERE id='failed-pin'").fetchone()
    await app.close()


async def test_workspace_unavailable_pin_and_explicit_remove_preserve_existing_chat_history(tmp_path):
    paths = [tmp_path / name for name in ('one', 'two')]
    for path in paths:
        path.mkdir()
    app = AppService(tmp_path / 'state', workspace=paths[0])
    await app.dispatch('session.create', {'workspace': str(paths[0])})
    one = app.state['selectedWorkspaceId']
    sid = app.state['selectedSessionId']
    await app.dispatch('workspace.add', {'path': str(paths[1])})
    next(row for row in app.state['workspaces'] if row['id'] == one)['available'] = False
    before = copy.deepcopy(app.state['sessions'])
    await app.dispatch('workspace.pin', {'id': one, 'pinned': True})
    assert app.state['pinnedWorkspaceIds'] == [one]
    assert all(row['workspaceId'] != one for row in snapshot({**app.state, 'view': {'navWorkspaceMode': 'recent'}})['rows'])
    assert app.state['sessions'] == before
    await app.dispatch('workspace.remove', {'id': one})
    assert app.state['pinnedWorkspaceIds'] == []
    assert app._session(sid)['workspace'] == str(paths[0])
    assert [row['id'] for row in app.state['sessions']] == [row['id'] for row in before]
    assert paths[0].is_dir()
    await app.close()

def test_archived_workspaces_search_restore_and_unavailable_history():
    value = state([workspace('active', '/work/current'), workspace('old', '/work/old', archived=True),
                   workspace('missing', '/work/missing', archived=True, available=False)],
                  [chat('saved', 'old')], 'active')
    active = browse(value, navWorkspaceMode='recent')
    assert [row['workspaceId'] for row in active['rows']] == ['active']
    archive = browse(value, navWorkspaceArchive='archived', navWorkspaceMode='recent')
    assert {row['workspaceId'] for row in archive['rows']} == {'old', 'missing'}
    assert next(row for row in archive['rows'] if row['workspaceId']=='missing')['available'] is False
    found = browse(value, navWorkspaceFilter='old')
    assert found['rows'][0]['workspaceId'] == 'old' and found['rows'][0]['chatCount'] == 1
    assert value['sessions'] == [chat('saved', 'old')]

async def test_workspace_archive_keeps_chat_and_files_and_does_not_change_selection(tmp_path):
    app = AppService(tmp_path/'data', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {'title':'Keep'})
        sid, wid = app.state['selectedSessionId'], app.state['selectedWorkspaceId']
        marker = tmp_path/'keep.txt'; marker.write_text('Keep')
        before = copy.deepcopy(app.state['sessions'])
        await app.dispatch('workspace.archive', {'id':wid})
        assert app.state['selectedSessionId'] == sid and app.state['selectedWorkspaceId'] == wid
        assert app.state['sessions'] == before and marker.read_text() == 'Keep'
        assert next(w for w in app.state['workspaces'] if w['id']==wid)['archived']
        await app.dispatch('workspace.restore', {'id':wid})
        assert not next(w for w in app.state['workspaces'] if w['id']==wid)['archived']
    finally:
        await app.close()
