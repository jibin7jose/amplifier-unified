"""Restriction compatibility must not invent package-selection authority."""
import json
import hashlib
from itertools import permutations

import pytest

from amplifier_web import runtime_source_policy as policy
from amplifier_web import runtime_source_alignment as alignment
from amplifier_web import runtime_policy_reconciliation as migration
from test_runtime_policy_reconciliation import selected, URL, NAME, git


@pytest.mark.parametrize('kind,accepted', [
    ('branch', True), ('rev', True), ('tag', False), ('pin', False),
    ('fork', False), ('conditional', False), ('local', False), ('subdirectory', False), ('dynamic', False),
])
async def test_optional_source_restricts_independently_selected_package(selected, kind, accepted):
    s = selected
    source = {'git': URL, 'branch': 'main'}
    if kind in {'rev', 'tag'}:
        source = {'git': URL, kind: 'main'}
    elif kind == 'pin':
        source = {'git': URL, 'rev': s.old}
    elif kind == 'fork':
        source['git'] += '-fork'
    elif kind == 'conditional':
        source['marker'] = "sys_platform == 'linux'"
    elif kind == 'local':
        source = {'path': '../..'}
    elif kind == 'subdirectory':
        source['subdirectory'] = 'different'
    from amplifier_web.runtime_environment import toml_value
    sibling = s.root / 'modules/skills'; sibling.mkdir(parents=True)
    raw = ('[project]\nname="fixture-skills"\nversion="1"\n' +
           ('dynamic=["dependencies"]\n' if kind == 'dynamic' else 'dependencies=[]\n') +
           '[project.optional-dependencies]\nremote=["' + NAME + '"]\n'
           '[tool.uv.sources]\n' + NAME + '=' + toml_value(source) + '\n').encode()
    (sibling / 'pyproject.toml').write_bytes(raw)
    git(s.root, 'add', 'modules'); git(s.root, 'commit', '-m', 'optional source')
    head = git(s.root, 'rev-parse', 'HEAD')
    s.marker.write_text(json.dumps({'git_url': URL, 'ref': 'main', 'commit': head}))
    s.batch.names['fixture-skills'] = sibling
    assert policy.declarations(raw) == {}  # Not authority to select this optional package.
    report = await s.collector.report(s.batch, s.config)
    assert report['packages'][NAME]['status'] == ('selected' if accepted else 'authored-constraint')
    checked = {NAME: {'url': URL, 'ref': 'main', 'subdirectory': '', 'revision': head}}
    if accepted:
        alignment.verify_checked_selection(checked, report, exact=True)
    else:
        with pytest.raises(ValueError) as error:
            alignment.verify_checked_selection(checked, report, exact=True)
        assert error.value.diagnostic_facts['reason'] == 'runtime-source-constraint'


@pytest.mark.parametrize('order', list(permutations(('rev', 'branch', 'tag'))))
async def test_conflicting_tag_cannot_be_erased_by_later_compatible_edges(selected, order):
    s = selected
    for index, kind in enumerate(order):
        path = s.root / ('modules/edge-' + str(index)); path.mkdir(parents=True)
        name = 'fixture-edge-' + str(index)
        value = ('[project]\nname="' + name + '"\nversion="1"\ndependencies=["' + NAME + '"]\n'
                 '[tool.uv.sources]\n' + NAME + '={git="' + URL + '",' + kind + '="main"}\n')
        (path / 'pyproject.toml').write_text(value)
        s.batch.names[name] = path
    git(s.root, 'add', 'modules'); git(s.root, 'commit', '-m', 'constraint alternatives')
    s.marker.write_text(json.dumps({'git_url': URL, 'ref': 'main', 'commit': git(s.root, 'rev-parse', 'HEAD')}))
    report = await s.collector.report(s.batch, s.config)
    assert report['packages'][NAME]['status'] == 'authored-constraint'


def test_same_manifest_constraint_alternatives_keep_unknown_and_do_not_select():
    raw = ('[project]\nname="fixture"\nversion="1"\n'
           '[project.optional-dependencies]\nremote=["' + NAME + '"]\n'
           '[tool.uv.sources]\n' + NAME + '=[{path="../local"},{git="' + URL + '",branch="main"}]\n').encode()
    assert policy.declarations(raw) == {}
    assert None in policy.constraint_declarations(raw)[NAME]


@pytest.mark.parametrize('alias_fork', [False, True])
@pytest.mark.parametrize('optional', [False, True])
async def test_runtime_git_sibling_keeps_its_dependency_but_not_redundant_local_uv_mapping(selected, alias_fork, optional):
    s = selected
    child = s.root / 'modules/tool'; child.mkdir(parents=True)
    (child / 'pyproject.toml').write_text(
        '[project]\nname="fixture-tool"\nversion="1"\n' +
        ('[project.optional-dependencies]\nrunner=' if optional else 'dependencies=') +
        '["' + NAME + ' @ git+' + URL + '@main"]\n'
        '[tool.uv.sources]\n' + NAME + '={path="../..",editable=true}\n' +
        (NAME.replace('-', '_') + '={git="' + URL + '-fork",branch="main"}\n' if alias_fork else ''))
    git(s.root, 'add', 'modules'); git(s.root, 'commit', '-m', 'runtime sibling and dev mapping')
    s.marker.write_text(json.dumps({'git_url': URL, 'ref': 'main', 'commit': git(s.root, 'rev-parse', 'HEAD')}))
    s.batch.names['fixture-tool'] = child
    report = await s.collector.report(s.batch, s.config)
    assert report['packages'][NAME]['status'] == ('authored-constraint' if alias_fork else 'selected')


@pytest.mark.parametrize('change', [
    'none', 'unlisted', 'glob', 'excluded', 'alias', 'unknown-extra', 'conditional',
    'runtime', 'specifier', 'override', 'local', 'nested',
])
async def test_workspace_development_sources_require_exact_selected_members(selected, change):
    s = selected
    child = s.root / 'modules/tool'; child.mkdir(parents=True)
    child_name = 'fixture-tool'
    (child / 'pyproject.toml').write_text('[project]\nname="fixture-tool"\nversion="1"\n')
    dev = [NAME + '[mcp,convert]', child_name]
    if change == 'unknown-extra': dev[1] += '[missing]'
    if change == 'conditional': dev[1] += "; python_version >= '3.13'"
    if change == 'specifier': dev[1] += '==1'
    members = ['.', 'modules/tool']
    if change == 'unlisted': members = ['.']
    if change == 'glob': members = ['.', 'modules/*']
    raw = ('[project]\nname="' + NAME + '"\nversion="1"\n' +
           ('dependencies=["fixture-tool"]\n' if change == 'runtime' else '') +
           '[project.optional-dependencies]\nmcp=[]\nconvert=[]\n'
           '[dependency-groups]\ndev=' + json.dumps(dev) + '\n' +
           ('[tool.uv]\nconstraint-dependencies=["fixture-tool==1"]\n' if change == 'override' else '') +
           '[tool.uv.sources]\n' + NAME + '={workspace=true}\nfixture-tool={workspace=true}\n' +
           ('fixture_tool={git="' + URL + '-fork",branch="main"}\n' if change == 'alias' else '') +
           '[tool.uv.workspace]\nmembers=' + json.dumps(members) + '\n' +
           ('exclude=["modules/tool"]\n' if change == 'excluded' else '')).encode()
    (s.root / 'pyproject.toml').write_bytes(raw)
    git(s.root, 'add', 'pyproject.toml', 'modules')
    git(s.root, 'commit', '-m', 'workspace source proof')
    head = git(s.root, 'rev-parse', 'HEAD')
    s.marker.write_text(json.dumps({'git_url': URL, 'ref': 'main', 'commit': head}))
    s.batch.names[child_name] = child
    if change == 'local':
        s.collector.local_sources.add(child)
    if change == 'nested':
        git(child, 'init', '-b', 'main')
        git(child, 'config', 'user.email', 'fixture@example.invalid')
        git(child, 'config', 'user.name', 'Fixture')
        git(child, 'remote', 'add', 'origin', URL + '-other')
        git(child, 'add', 'pyproject.toml')
        git(child, 'commit', '-m', 'independent checkout')
        (child / '.amplifier_cache_meta.json').write_text(json.dumps({
            'git_url': URL + '-other', 'ref': 'main', 'commit': git(child, 'rev-parse', 'HEAD')}))
        s.collector.references.add((URL + '-other', 'main'))
    report = await s.collector.report(s.batch, s.config)
    assert policy.declarations(raw) == {}  # Workspace/dev entries select nothing.
    if change == 'none':
        assert report['packages'][NAME]['status'] == 'selected'
        assert report['packages'][child_name]['status'] == 'selected'
        alignment.verify_checked_selection({
            NAME: {'url': URL, 'ref': 'main', 'subdirectory': '', 'revision': head},
            child_name: {'url': URL, 'ref': 'main', 'subdirectory': 'modules/tool', 'revision': head},
        }, report, exact=True)
    else:
        assert report['packages'][child_name]['status'] != 'selected'


@pytest.mark.parametrize('fragment', ['unknown=value', 'subdirectory=&subdirectory=modules/x'])
def test_optional_direct_git_unknown_fragment_remains_protective(fragment):
    raw = ('[project]\nname="fixture"\nversion="1"\n'
           '[project.optional-dependencies]\nremote=["' + NAME + ' @ git+' + URL + '@main#' + fragment + '"]\n').encode()
    assert policy.declarations(raw) == {}
    assert None in policy.constraint_declarations(raw)[NAME]


def dependent_constraint(s, declaration, *, suffix='routing', uv_source=None, optional=False):
    """A separate actual Git source can pin A without changing A's own SHA."""
    root = s.stage / ('foundation/cache/' + suffix)
    root.mkdir(parents=True)
    name, url = 'fixture-' + suffix, URL + '-' + suffix
    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.email', 'fixture@example.invalid')
    git(root, 'config', 'user.name', 'Fixture')
    git(root, 'remote', 'add', 'origin', url)
    content = '[project]\nname="' + name + '"\nversion="1"\n'
    if optional:
        content += '[project.optional-dependencies]\nremote=' + json.dumps([declaration]) + '\n'
    else:
        content += 'dependencies=' + json.dumps([declaration]) + '\n'
    if uv_source is not None:
        from amplifier_web.runtime_environment import toml_value
        content += '[tool.uv.sources]\n' + NAME + '=' + toml_value(uv_source) + '\n'
    path = root / 'pyproject.toml'
    path.write_text(content)
    git(root, 'add', 'pyproject.toml')
    git(root, 'commit', '-m', 'authored dependency constraint')
    revision = git(root, 'rev-parse', 'HEAD')
    (root / '.amplifier_cache_meta.json').write_text(json.dumps({
        'git_url': url, 'ref': 'main', 'commit': revision}))
    s.collector.observe('git+' + url + '@main', s.config)
    s.batch.names[name] = root
    return {'package': name, 'url': url, 'revision': revision, 'subdirectory': '',
            'manifestSha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'declarations': policy.constraint_declarations(path.read_bytes(), runtime_batch=True)[NAME]}


@pytest.mark.parametrize('optional', [False, True])
@pytest.mark.parametrize('spelling', ['pep508', 'uv-rev'])
async def test_exact_authored_pin_allows_only_same_selected_and_checked_commit(selected, optional, spelling):
    s = selected
    direct = NAME + ' @ git+' + URL + '@' + s.old
    origin = dependent_constraint(s, direct if spelling == 'pep508' else NAME,
        uv_source={'git': URL, 'rev': s.old} if spelling == 'uv-rev' else None, optional=optional)
    checked = {NAME: {'url': URL, 'ref': 'main', 'subdirectory': '', 'revision': s.old}}
    (s.stage / 'runtime-sources.json').write_text(json.dumps(checked))
    before = (s.root / 'pyproject.toml').read_bytes(), s.marker.read_bytes()
    report = await alignment.checked_batch_sources(s.batch, s.collector, s.config, checked)
    package = report['packages'][NAME]
    assert package['status'] == 'explicit-pin'
    assert package['ref'] == 'main' and package['revision'] == s.old
    assert package['authoredConstraints'] == [origin]
    # The normal alignment/install preflight admits A, but never grants moving
    # permission or legacy migration merely because main happens to equal A.
    await alignment.align_checked_sources(s.manager, s.stage, selection=report)
    alignment.verify_checked_selection(checked, report, exact=True)
    migrated, decisions = migration.selected_policies({
        NAME: {'url': URL, 'ref': s.old, 'current': s.old, 'subdirectory': ''}}, report)
    assert migrated == {} and decisions == {NAME: 'explicit-pin'}
    assert alignment.protected_selection(s.root, report)
    assert git(s.root, 'rev-parse', 'HEAD') == s.old
    assert before == ((s.root / 'pyproject.toml').read_bytes(), s.marker.read_bytes())
    assert not any(any(verb in cmd for verb in ('fetch', 'checkout', 'reset', 'clean', 'install')) for cmd in s.commands)


@pytest.mark.parametrize('drift', ['checked-advance', 'selected-advance', 'both-advance'])
async def test_exact_authored_pin_refuses_branch_advance_before_checkout_or_install(selected, drift):
    s = selected
    dependent_constraint(s, NAME + ' @ git+' + URL + '@' + s.old)
    if drift in {'selected-advance', 'both-advance'}:
        git(s.root, 'checkout', '--detach', s.new)
        s.marker.write_text(json.dumps({'git_url': URL, 'ref': 'main', 'commit': s.new}))
    checked = {NAME: {'url': URL, 'ref': 'main', 'subdirectory': '',
                      'revision': s.old if drift == 'selected-advance' else s.new}}
    (s.stage / 'runtime-sources.json').write_text(json.dumps(checked))
    before = (git(s.root, 'rev-parse', 'HEAD'), s.marker.read_bytes())
    installs = []
    # Same ordered collect -> pre-alignment guard -> alignment -> exact guard
    # used by update_probe. The install spy must remain unreachable on failure.
    with pytest.raises(ValueError):
        report = await alignment.checked_batch_sources(s.batch, s.collector, s.config, checked)
        await alignment.align_checked_sources(s.manager, s.stage, selection=report)
        alignment.verify_checked_selection(checked, report, exact=True)
        installs.append('unreachable')
    assert not installs
    assert before == (git(s.root, 'rev-parse', 'HEAD'), s.marker.read_bytes())
    assert not any(any(verb in cmd for verb in ('fetch', 'checkout', 'reset', 'clean', 'install')) for cmd in s.commands)


@pytest.mark.parametrize('case', ['fork', 'subdirectory', 'abbreviated', 'tag', 'conditional',
                                  'extra', 'local', 'unknown-fragment', 'unknown-field', 'branch-sha'])
async def test_exact_pin_does_not_excuse_other_authored_constraints(selected, case):
    s = selected
    target = URL + '@' + s.old
    uv = None
    declaration = NAME + ' @ git+' + target
    if case == 'fork': declaration = NAME + ' @ git+' + URL + '-fork@' + s.old
    elif case == 'subdirectory': declaration += '#subdirectory=different'
    elif case == 'abbreviated': declaration = NAME + ' @ git+' + URL + '@' + s.old[:12]
    elif case == 'conditional': declaration += " ; python_version >= '3.13'"
    elif case == 'extra': declaration = NAME + '[feature] @ git+' + target
    elif case == 'unknown-fragment': declaration += '#unknown=value'
    elif case in {'tag', 'branch-sha'}:
        declaration = NAME
        uv = {'git': URL, 'tag' if case == 'tag' else 'branch': s.old}
    elif case == 'local':
        declaration = NAME
        uv = {'path': '../fixture'}
    elif case == 'unknown-field':
        declaration = NAME
        uv = {'git': URL, 'rev': s.old, 'unknown': True}
    origin = dependent_constraint(s, declaration, uv_source=uv)
    report = await s.collector.report(s.batch, s.config)
    assert report['packages'][NAME]['status'] == 'authored-constraint'
    assert report['packages'][NAME]['authoredConstraints'] == [origin]
    with pytest.raises(ValueError) as caught:
        alignment.verify_checked_selection({NAME: {
            'url': URL, 'ref': 'main', 'subdirectory': '', 'revision': s.old}}, report)
    assert caught.value.diagnostic_facts['reason'] == 'runtime-source-constraint'


@pytest.mark.parametrize('order', list(permutations(('pin', 'branch', 'conflict'))))
async def test_exact_pin_checks_all_declarations_without_last_writer_wins(selected, order):
    s = selected
    for i, kind in enumerate(order):
        ref = s.old if kind == 'pin' else 'main' if kind == 'branch' else s.new
        dependent_constraint(s, NAME + ' @ git+' + URL + '@' + ref, suffix='edge-' + str(i))
    report = await s.collector.report(s.batch, s.config)
    assert report['packages'][NAME]['status'] == 'authored-constraint'
    assert len(report['packages'][NAME]['authoredConstraints']) == 3
    with pytest.raises(ValueError):
        alignment.verify_checked_selection({NAME: {
            'url': URL, 'ref': 'main', 'subdirectory': '', 'revision': s.old}}, report)


async def test_exact_pin_and_compatible_branch_keep_both_authored_origins(selected):
    s = selected
    origins = [dependent_constraint(s, NAME + ' @ git+' + URL + '@' + ref, suffix='edge-' + str(i))
               for i, ref in enumerate((s.old, 'main', s.old))]
    report = await alignment.checked_batch_sources(s.batch, s.collector, s.config, {
        NAME: {'url': URL, 'ref': 'main', 'subdirectory': '', 'revision': s.old}})
    assert report['packages'][NAME]['status'] == 'explicit-pin'
    assert report['packages'][NAME]['authoredConstraints'] == origins


@pytest.mark.parametrize('change', ['missing', 'repository', 'ref', 'subdirectory'])
async def test_compatible_authored_pin_still_requires_exact_checked_identity(selected, change):
    s = selected
    dependent_constraint(s, NAME + ' @ git+' + URL + '@' + s.old)
    checked = {NAME: {'url': URL, 'ref': 'main', 'subdirectory': '', 'revision': s.old}}
    if change == 'missing':
        checked.clear()
    elif change == 'repository':
        checked[NAME]['url'] += '-fork'
    else:
        checked[NAME][change] = 'different'
    pointer = s.home / 'updates/active.json'
    failure = s.home / 'updates/prior-failure.json'
    pointer.write_text('{"current":"serving","previous":"rollback"}')
    failure.write_text('{"status":"failed","attemptId":"retained"}')
    before = (s.marker.read_bytes(), pointer.read_bytes(), failure.read_bytes())
    with pytest.raises(ValueError):
        await alignment.checked_batch_sources(s.batch, s.collector, s.config, checked)
    assert before == (s.marker.read_bytes(), pointer.read_bytes(), failure.read_bytes())
    assert not any(any(verb in cmd for verb in ('fetch', 'checkout', 'reset', 'clean', 'install')) for cmd in s.commands)
