"""An agent read prepares retained metadata once per synchronous projection."""
from copy import deepcopy
import json

import pytest

from test_shell_modules import service
from test_cold_display import app, add_history


@pytest.mark.parametrize('retained', [4, 4000])
async def test_warm_session_page_does_not_encode_omitted_history(service, monkeypatch, retained):
    from amplifier_web import agent_state

    visits = []

    class Messages(list):
        def __iter__(self):
            for item in super().__iter__():
                visits.append(item['id'])
                yield item

    owner = service._state['sessions'][0]
    owner['messages'] = Messages([
        {'id': f'm-{n}', 'role': 'assistant', 'text': 'Retained synthetic text. ' * 50}
        for n in range(retained)])
    # Prepare the canonical notification projection separately: its initial
    # history scan is not preview encoding. Repeated reads reuse that projection.
    await service.app_bridge('get_state', {'path': '/revision'}, 'alpha')
    visits.clear()
    revision = service._state['revision']
    encoded = []
    size = agent_state._size

    def measured_size(value):
        length = size(value)
        encoded.append(length)
        return length

    monkeypatch.setattr(agent_state, '_size', measured_size)
    page = await service.app_bridge('get_state', {'path': '/sessions', 'limit': 50}, 'alpha')
    # A warm two-owner page must not traverse/encode all historical messages merely
    # to decide they do not fit. These budgets allow repeated bounded previews.
    assert len(visits) < 100
    assert sum(encoded) < 100_000
    assert page['revision'] == revision
    assert page['total'] == 2
    assert 'Keep this unsent message' not in json.dumps(page)
    selected = next(item for item in page['items'] if item['value'].get('id') == 'alpha'
                    or item['value'].get('preview', {}).get('id') == 'alpha')
    path = selected['path'] + '/messages'
    args = {'path': path, 'offset': retained - 1, 'limit': 1,
            'revision': revision, 'directoryRevision': page['directoryRevision']}
    detail = await service.app_bridge('get_state', args, 'alpha')
    assert detail['items'][0]['value'] == owner['messages'][-1]
    assert detail['total'] == retained and detail['nextOffset'] is None
    owner['messages'][-1]['text'] = 'Current nested edit, without a publication.'
    updated = await service.app_bridge('get_state', args, 'alpha')
    assert updated['items'][0]['value'] == owner['messages'][-1]
    assert service._state['revision'] == revision


@pytest.mark.parametrize('value', [
    None, True, 123.5, [], {},
    '日本語 café 😀' * 40,
    '\n"\\' * 100,
    {'escaped/key': ['é' * 300, {'~nested': '\t' * 100}]},
    list(range(100)),
])
def test_preview_preserves_exact_json_character_boundary(value):
    from amplifier_web.agent_state import _preview

    length = len(json.dumps(value, ensure_ascii=False))
    assert _preview(value, '/value', length) is value
    below = _preview(value, '/value', length - 1, reference_key='$detail')
    assert below['$detail'] == '/value'
    assert below['type'] == type(value).__name__


@pytest.mark.parametrize('characters', [5000, 1_000_000])
def test_preview_does_not_encode_an_omitted_large_leaf(monkeypatch, characters):
    from amplifier_web import agent_state

    value = {'text': 'é' * characters}
    encoded = []
    size = agent_state._size

    def measured_size(item):
        length = size(item)
        encoded.append(length)
        return length

    monkeypatch.setattr(agent_state, '_size', measured_size)
    preview = agent_state._preview(value, '/source')
    assert sum(encoded) < 10_000
    assert preview['$statePath'] == '/source'
    text = preview['preview']['text']
    assert text['$statePath'] == '/source/text' and text['size'] == characters
    assert value == {'text': 'é' * characters}


@pytest.mark.parametrize('retained', [1, 90])
@pytest.mark.parametrize('args,preparations', [({'path': '/revision'}, 1), ({}, 2)])
async def test_agent_read_does_not_recopy_questions_for_each_derived_section(
        service, retained, args, preparations):
    visits = []

    class Questions(list):
        def __deepcopy__(self, memo):
            visits.extend(item['id'] for item in self)
            return deepcopy(list(self), memo)

    row = service._state['sessions'][1]
    row['questions'] = Questions([
        {'id': f'q-{n}', 'status': 'answered', 'required': True,
         'revision': 1, 'prompt': 'Retained resolved question. ' * 100}
        for n in range(retained)])
    original = deepcopy(list(row['questions']))
    service._state['selectedSessionId'] = 'alpha'
    service._state['selectedWorkspaceId'] = 'one'

    result = await service.app_bridge('get_state', args, 'alpha')

    if args:
        assert result['value'] == service._state['revision']
    else:
        assert result['session']['id'] == 'alpha'
        assert {row['id'] for row in result['conversations']} == {'alpha', 'beta'}
    # Overview also takes an independent frozen directory snapshot. Its own
    # preparation is legitimate; repeating it for every derived section is not.
    assert len(visits) <= retained * preparations
    assert row['questions'] == original


async def test_new_agent_read_sees_nested_changes_without_publication(service):
    row = service._state['sessions'][1]
    row['questions'] = [{'id': 'question', 'status': 'pending', 'required': True,
                         'revision': 1, 'prompt': 'Choose a scope'}]
    revision = service._state['revision']

    async def notices():
        # The mutation owner invalidates derived views; neither a publication
        # nor a new app revision is needed to observe current metadata.
        service.projections.invalidate(state=service.state, session_ids={'beta'})
        return await service.app_bridge('get_state', {'path': '/attention/unread'}, 'alpha')

    assert (await notices())['value'] == 1
    row['questions'][0]['status'] = 'answered'
    assert (await notices())['value'] == 0
    row['questions'][0].update(status='pending', prompt='Choose the revised scope', revision=2)
    assert (await notices())['value'] == 1
    assert service._state['revision'] == revision


def test_complete_compatibility_read_still_hydrates_outside_read_frame(app, monkeypatch):
    row = add_history(app, 'complete-read')
    expected = deepcopy(row['messages'])
    monkeypatch.setattr(app.cold_display, 'RECENT_LIMIT', 0)
    app.cold_display.retire(force=True)
    assert app.cold_display.is_cold(row)
    from amplifier_web import session_projection
    original = session_projection.hydrate
    hydrated = []

    def hydrate(*args):
        assert app.directory._read_frame.get() is None
        hydrated.append(True)
        return original(*args)

    monkeypatch.setattr(session_projection, 'hydrate', hydrate)
    result = app.get_state()
    actual = next(owner for owner in result['sessions'] if owner['id'] == row['id'])
    assert hydrated
    assert actual['messages'] == expected
