"""Host instruction policy through Foundation's real, fresh prompt renderer."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_foundation.bundle import Bundle, PreparedBundle, BundleModuleResolver
from amplifier_web.host.mentions import include_instruction_files


def factory(bundle, workspace):
    prepared = PreparedBundle(bundle.to_mount_plan(), BundleModuleResolver({}), bundle)
    session = SimpleNamespace(coordinator=SimpleNamespace(hooks=SimpleNamespace(emit=AsyncMock())))
    return prepared.create_system_prompt_factory(session, session_cwd=workspace)


@pytest.fixture(autouse=True)
def instruction_home(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    original_expanduser = Path.expanduser
    monkeypatch.setattr(Path, 'expanduser', lambda self: home / str(self)[2:]
                        if str(self).startswith('~/') else original_expanduser(self))
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    return home


@pytest.mark.parametrize('body', ['', 'Original root instructions.'])
async def test_optional_instruction_files_reload_without_mutating_cached_bundle(tmp_path, body):
    home, workspace, cache = (tmp_path / name for name in ('home', 'workspace', 'bundle-cache'))
    for path in (home / '.amplifier', workspace / '.amplifier', cache / '.amplifier'):
        path.mkdir(parents=True)
    (cache / '.amplifier/AGENTS.md').write_text('CACHE-WORKSPACE-MUST-NOT-LOAD')
    bundle = Bundle(name='custom', instruction=body, base_path=cache)
    composed = include_instruction_files(bundle, execution_workspace=workspace)
    render = factory(composed, workspace)
    assert bundle.instruction == body
    assert include_instruction_files(composed, execution_workspace=workspace).context == composed.context
    assert 'CACHE-WORKSPACE-MUST-NOT-LOAD' not in await render()
    (home / '.amplifier/AGENTS.md').write_text('GLOBAL-JOURNAL-POLICY\n@~/.amplifier/journal-rules.md')
    (home / '.amplifier/journal-rules.md').write_text('NESTED-JOURNAL-DESTINATION')
    (workspace / '.amplifier/AGENTS.md').write_text('PROJECT-JOURNAL-POLICY')
    (workspace / 'AGENTS.md').write_text('WORKSPACE-ROOT-POLICY')
    first = await render()
    for sentinel in ('GLOBAL-JOURNAL-POLICY', 'NESTED-JOURNAL-DESTINATION', 'PROJECT-JOURNAL-POLICY', 'WORKSPACE-ROOT-POLICY'):
        assert first.count(sentinel) == 1
    assert 'CACHE-WORKSPACE-MUST-NOT-LOAD' not in first
    (home / '.amplifier/AGENTS.md').write_text('UPDATED-GLOBAL-POLICY')
    second = await render()
    assert 'UPDATED-GLOBAL-POLICY' in second and 'GLOBAL-JOURNAL-POLICY' not in second
    assert 'NESTED-JOURNAL-DESTINATION' not in second
    (workspace / '.amplifier/AGENTS.md').unlink()
    assert 'PROJECT-JOURNAL-POLICY' not in await render()


async def test_existing_instruction_mentions_and_each_session_workspace_are_preserved(tmp_path):
    first, second = tmp_path / 'first', tmp_path / 'second'
    for path, text in ((first, 'FIRST-WORKSPACE'), (second, 'SECOND-WORKSPACE')):
        (path / '.amplifier').mkdir(parents=True)
        (path / '.amplifier/AGENTS.md').write_text(text)
    root = Bundle(name='work', instruction='Keep the root.\n@.amplifier/AGENTS.md')
    included = include_instruction_files(root, execution_workspace=first)
    assert included.instruction.count('@.amplifier/AGENTS.md') == 1
    assert included.instruction.startswith(root.instruction)
    first_prompt = await factory(included, first)()
    second_prompt = await factory(include_instruction_files(root, execution_workspace=second), second)()
    assert 'FIRST-WORKSPACE' in first_prompt and 'SECOND-WORKSPACE' not in first_prompt
    assert 'SECOND-WORKSPACE' in second_prompt and 'FIRST-WORKSPACE' not in second_prompt


async def test_root_workspace_guidance_and_scratch_reload_through_entry_point(tmp_path):
    from amplifier_web.workspace_starters import DEVELOPMENT_INSTRUCTIONS
    workspace = tmp_path / 'container'
    (workspace / '.amplifier').mkdir(parents=True)
    (workspace / 'AGENTS.md').write_text(DEVELOPMENT_INSTRUCTIONS)
    (workspace / '.amplifier/AGENTS.md').write_text('@../AGENTS.md\n@../SCRATCH.md\n')
    (workspace / 'SCRATCH.md').write_text('ROOT-SCRATCH-INITIAL')
    (workspace / '.amplifier/SCRATCH.md').write_text('WRONG-SCRATCH')
    render = factory(include_instruction_files(Bundle(name='work', instruction='Root bundle'),
                                             execution_workspace=workspace), workspace)
    first = await render()
    assert 'The workspace root is never the source root' in first
    assert first.count('ROOT-SCRATCH-INITIAL') == 1
    assert 'WRONG-SCRATCH' not in first
    (workspace / 'SCRATCH.md').write_text('ROOT-SCRATCH-UPDATED')
    second = await render()
    assert 'ROOT-SCRATCH-UPDATED' in second and 'ROOT-SCRATCH-INITIAL' not in second


async def test_scratch_detail_links_do_not_autoload_history_or_receipts(tmp_path):
    from amplifier_web.workspace_starters import DEVELOPMENT_INSTRUCTIONS, SCRATCH_TEMPLATE
    workspace = tmp_path / 'workspace'
    (workspace / '.amplifier').mkdir(parents=True)
    (workspace / 'working-files').mkdir()
    (workspace / 'working-files/archive.md').write_text('SUPERSEDED-HOLD-MUST-NOT-LOAD')
    (workspace / 'working-files/checks.json').write_text('{"private-evidence":"FULL-RECEIPT-MUST-NOT-LOAD"}')
    (workspace / 'AGENTS.md').write_text(DEVELOPMENT_INSTRUCTIONS)
    (workspace / '.amplifier/AGENTS.md').write_text('@../AGENTS.md\n@../SCRATCH.md\n')
    scratch = workspace / 'SCRATCH.md'
    scratch.write_text(SCRATCH_TEMPLATE + '\nCURRENT-DECISION: ready for review.\n'
                       '[Earlier detail](working-files/archive.md)\n'
                       'Check receipt: working-files/checks.json\n')
    render = factory(include_instruction_files(Bundle(name='work', instruction='Root'),
                                             execution_workspace=workspace), workspace)
    first = await render()
    assert first.count('CURRENT-DECISION: ready for review.') == 1
    assert 'SUPERSEDED-HOLD-MUST-NOT-LOAD' not in first
    assert 'FULL-RECEIPT-MUST-NOT-LOAD' not in first
    assert 'working-files/archive.md' in first
    scratch.write_text(SCRATCH_TEMPLATE + '\nCURRENT-DECISION: accepted.\n')
    second = await render()
    assert 'CURRENT-DECISION: accepted.' in second
    assert 'CURRENT-DECISION: ready for review.' not in second
    assert 'working-files/archive.md' not in second
    assert (workspace / 'working-files/archive.md').read_text() == 'SUPERSEDED-HOLD-MUST-NOT-LOAD'


@pytest.mark.parametrize('name', ['work', '', None, 'root with spaces', 'root:other', 'root/path', 'root@other'])
async def test_selected_home_and_all_cwd_files_need_no_root_namespace(tmp_path, name):
    home, workspace, cache = (tmp_path / part for part in ('selected home', 'work tree', 'cache'))
    for path in (home, workspace / '.amplifier', cache):
        path.mkdir(parents=True)
    files = [home / 'AGENTS.md', workspace / '.amplifier/AGENTS.md', workspace / 'AGENTS.md']
    for path, marker in zip(files, ('INSTANCE-GLOBAL', 'CWD-PROJECT', 'CWD-ROOT')):
        path.write_text(marker)
    (home / 'notes.md').write_text('INSTANCE-NESTED')
    files[0].write_text('INSTANCE-GLOBAL\n@./notes.md')
    (cache / 'AGENTS.md').write_text('CACHE-DECOY')
    bundle = Bundle(name=name, instruction='Authored instructions', base_path=cache)
    included = include_instruction_files(bundle, home, workspace)
    assert list(included.context.values()) == files
    assert included.instruction == bundle.instruction
    assert included.name == name and included.source_base_paths == bundle.source_base_paths
    assert bundle.context == {}
    prompt = await factory(included, workspace)()
    for marker in ('INSTANCE-GLOBAL', 'INSTANCE-NESTED', 'CWD-PROJECT', 'CWD-ROOT'):
        assert prompt.count(marker) == 1
    assert 'CACHE-DECOY' not in prompt


async def test_nested_bare_reference_keeps_workspace_semantics(tmp_path):
    home, workspace = tmp_path / 'selected home', tmp_path / 'work tree'
    home.mkdir()
    workspace.mkdir()
    (home / 'AGENTS.md').write_text('INSTANCE-GLOBAL\n@notes.md\n@./sibling.md')
    (home / 'notes.md').write_text('WRONG-BARE-REFERENCE-BASE')
    (home / 'sibling.md').write_text('EXPLICIT-SIBLING')
    (workspace / 'notes.md').write_text('BARE-WORKSPACE-REFERENCE')
    render = factory(include_instruction_files(Bundle(name='work'), home, workspace), workspace)
    prompt = await render()
    assert prompt.count('BARE-WORKSPACE-REFERENCE') == 1
    assert prompt.count('EXPLICIT-SIBLING') == 1
    assert 'WRONG-BARE-REFERENCE-BASE' not in prompt
    (workspace / 'notes.md').write_text('BARE-WORKSPACE-UPDATED')
    second = await render()
    assert 'BARE-WORKSPACE-UPDATED' in second
    assert 'BARE-WORKSPACE-REFERENCE' not in second


async def test_authored_tilde_and_context_order_attribution_dedup_are_preserved(tmp_path, instruction_home):
    physical, selected, workspace = instruction_home / '.amplifier', tmp_path / 'selected', tmp_path / 'work'
    for path in (physical, selected, workspace / '.amplifier'):
        path.mkdir(parents=True)
    (physical / 'AGENTS.md').write_text('AUTHORED-PHYSICAL')
    (selected / 'AGENTS.md').write_text('SELECTED-GLOBAL')
    (workspace / '.amplifier/AGENTS.md').write_text('PROJECT-GUIDANCE')
    (workspace / 'AGENTS.md').write_text('ROOT-GUIDANCE')
    awareness = workspace / 'awareness.md'
    awareness.write_text('EXISTING-AWARENESS\n@AGENTS.md')
    instruction = 'Authored literal stays.\n@~/.amplifier/AGENTS.md'
    bundle = Bundle(name='work', instruction=instruction, context={'awareness': awareness})
    included = include_instruction_files(bundle, selected, workspace)
    assert included.instruction == instruction
    assert list(included.context)[-1] == 'awareness'
    prompt = await factory(included, workspace)()
    markers = ('AUTHORED-PHYSICAL', 'SELECTED-GLOBAL', 'PROJECT-GUIDANCE', 'ROOT-GUIDANCE', 'EXISTING-AWARENESS')
    assert [prompt.index(marker) for marker in markers] == sorted(prompt.index(marker) for marker in markers)
    for marker in markers:
        assert prompt.count(marker) == 1
    assert f'__unified_instruction_global → {selected / "AGENTS.md"}' in prompt
    assert '@~/.amplifier/AGENTS.md' in prompt
    assert bundle.context == {'awareness': awareness}


async def test_alias_collisions_and_repeated_binding_do_not_retarget_authored_references(tmp_path):
    home, workspace = tmp_path / 'home', tmp_path / 'work'
    home.mkdir()
    workspace.mkdir()
    authored = workspace / 'authored.md'
    authored.write_text('AUTHORED-FILE')
    (home / 'AGENTS.md').write_text('HOST-GLOBAL')
    stem = '__unified_instruction_global'
    bundle = Bundle(name='work', instruction=f'@work:{stem}\n@work:{stem}_2',
                    context={stem: authored}, _pending_context={f'work:{stem}_3': 'missing:rules'})
    included = include_instruction_files(bundle, home, workspace)
    repeated = include_instruction_files(included, home, workspace)
    assert included.context == repeated.context
    assert list(included.context) == list(repeated.context)
    assert included.context[stem] == authored
    assert included.context[f'{stem}_4'] == home / 'AGENTS.md'
    assert f'{stem}_2' not in included.context and f'{stem}_3' not in included.context
    assert included.instruction == bundle.instruction
    assert included._pending_context == bundle._pending_context
    assert included.source_base_paths == bundle.source_base_paths
    prompt = await factory(included, workspace)()
    assert prompt.count('AUTHORED-FILE') == 1 and prompt.count('HOST-GLOBAL') == 1


async def test_same_file_authored_and_default_is_loaded_once(tmp_path, monkeypatch):
    (tmp_path / '.amplifier').mkdir()
    (tmp_path / 'AGENTS.md').write_text('SAME-ROOT')
    (tmp_path / '.amplifier/AGENTS.md').write_text('PROJECT\n@../AGENTS.md')
    monkeypatch.chdir(tmp_path)
    bundle = Bundle(name='work', instruction='@AGENTS.md')
    render = factory(include_instruction_files(bundle), tmp_path)
    assert (await render()).count('SAME-ROOT') == 1
    (tmp_path / 'AGENTS.md').unlink()
    assert 'SAME-ROOT' not in await render()


async def test_bound_execution_guidance_is_not_retargeted_by_render_workspace(tmp_path):
    execution, rendering = tmp_path / 'execution', tmp_path / 'rendering'
    for directory, marker in ((execution, 'EXECUTION-GUIDANCE'), (rendering, 'RENDER-DECOY')):
        directory.mkdir()
        (directory / 'AGENTS.md').write_text(marker)
    included = include_instruction_files(Bundle(name='work'), execution_workspace=execution)
    render = factory(included, rendering)
    assert (await render()).count('EXECUTION-GUIDANCE') == 1
    assert 'RENDER-DECOY' not in await render()
    (execution / 'AGENTS.md').write_text('EDITED-EXECUTION-GUIDANCE')
    updated = await render()
    assert 'EDITED-EXECUTION-GUIDANCE' in updated and 'RENDER-DECOY' not in updated


async def test_explicit_workspace_context_follows_independent_checkouts_without_ancestors(tmp_path):
    workspace = tmp_path / 'associated'
    (workspace / '.amplifier').mkdir(parents=True)
    (workspace / 'AGENTS.md').write_text('SHARED-RULE\n@./SCRATCH.md')
    (workspace / '.amplifier/AGENTS.md').write_text('@../AGENTS.md')
    (workspace / 'SCRATCH.md').write_text('CURRENT-DECISION\n[Details](archive.md)')
    (workspace / 'archive.md').write_text('OBSOLETE-HOLD')
    (tmp_path / 'AGENTS.md').write_text('UNRELATED-ANCESTOR')
    root = Bundle(name='work')
    renderers = []
    for name in ('worker-one', 'worker-two'):
        checkout = tmp_path / name
        checkout.mkdir()
        (checkout / 'AGENTS.md').write_text(f'RULE-FOR-{name.upper()}')
        included = include_instruction_files(root, execution_workspace=checkout, workspace=workspace)
        assert included.context == include_instruction_files(
            included, execution_workspace=checkout, workspace=workspace).context
        render = factory(included, checkout)
        renderers.append(render)
        prompt = await render()
        for text in ('SHARED-RULE', 'CURRENT-DECISION', f'RULE-FOR-{name.upper()}'):
            assert prompt.count(text) == 1
        assert 'OBSOLETE-HOLD' not in prompt and 'UNRELATED-ANCESTOR' not in prompt
        assert str(workspace / 'AGENTS.md') in prompt
        assert str(checkout / 'AGENTS.md') in prompt
    (workspace / 'SCRATCH.md').write_text('REVISED-DECISION')
    for render in renderers:
        prompt = await render()
        assert 'REVISED-DECISION' in prompt and 'CURRENT-DECISION' not in prompt
    assert root.context == {}


async def test_same_workspace_and_execution_keep_original_binding_and_render_once(tmp_path):
    (tmp_path / 'AGENTS.md').write_text('ONE-SET-OF-RULES')
    bundle = Bundle(name='work')
    old = include_instruction_files(bundle, execution_workspace=tmp_path)
    explicit = include_instruction_files(bundle, execution_workspace=tmp_path, workspace=tmp_path)
    assert explicit.context == old.context
    assert (await factory(explicit, tmp_path)()).count('ONE-SET-OF-RULES') == 1
