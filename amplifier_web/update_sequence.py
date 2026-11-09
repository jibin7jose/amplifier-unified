"""App-first update phases and the included component inventory.

Read shipped declarations and effective app choices. Checking never composes bundles,
install packages, or activate any cached source.
"""
from pathlib import Path
import hashlib
import json
import tomllib
from urllib.parse import urlsplit


def git_source(uri):
    """Read Foundation-consumed declarations without resolving their sources.

    Foundation's parse_uri defaults an omitted Git ref to main, not remote
    HEAD. Keep explicit refs verbatim; source_key must not alias the two.
    """
    if not isinstance(uri, str) or not uri.startswith('git+'):
        return None
    parsed = urlsplit(uri[4:])
    path, separator, ref = parsed.path.rpartition('@')
    return {'url': parsed._replace(path=path if separator else parsed.path, fragment='').geturl(),
            'ref': ref if separator else 'main'}


def included_sources():
    from .runtime_environment import declared_manifest, package_name
    from .builtin_behaviors import resource_root
    import yaml
    content = declared_manifest()
    manifest = tomllib.loads(content.decode())
    packages = {package_name(name) for name in manifest.get('tool', {}).get('uv', {}).get('sources', {})}
    for value in manifest['project']['dependencies']:
        name, separator, uri = value.partition(' @ ')
        if separator and git_source(uri):
            packages.add(package_name(name))
    from .runtime_registry_updates import open_policy
    if open_policy(content) is not None:
        packages.add('amplifier-core')
    # Includes and module sources in shipped app behaviors are required even
    # before the first conversation has populated their source cache.
    sources = {}
    def walk(value):
        if isinstance(value, dict):
            for child in value.values():walk(child)
        elif isinstance(value, list):
            for child in value:walk(child)
        elif source := git_source(value):
            sources[(source['url'], source['ref'])] = source
    for path in (resource_root() / 'behaviors').glob('*.yaml'):
        walk(yaml.safe_load(path.read_text()))
    return packages, list(sources.values())


def app_selection(home, *, config=None, original_home=None):
    """Read enabled app choices only; never load remote or user-authored bundles."""
    from .host.config import read_config
    from .host.bundle_paths import configured_bundle_reference, local_bundle_path
    from .builtin_behaviors import resolve_builtin_behavior, resource_root
    from .runtime_profiles import configuration_key
    from .updates import source_key
    from urllib.parse import parse_qs, unquote
    import yaml

    config = config or read_config(home, home=home, global_only=True)
    registrations = config.registrations
    references, sources = [], {}

    def selected(reference):
        reference = configured_bundle_reference(config, reference, registrations.get)
        # BundleRegistry.load substitutes one exact registration, not an alias
        # graph. Do not invent recursive selection that composition won't use.
        reference = registrations.get(reference) or reference
        if (not git_source(reference) and local_bundle_path(config, reference) is None
                and not reference.startswith(('/', './', '../', '~', 'file:', 'https:', 'http:'))):
            raise ValueError('Enabled app behavior has an unresolved source alias')
        references.append(reference)
        if source := git_source(reference):
            sources[source_key(source['url'], source['ref'])] = source
        return reference

    def shipped(value):
        # Only declarations, not arbitrary plugin config values, select code.
        if not isinstance(value, dict):
            return
        for include in value.get('includes', []) or []:
            selected(include.get('bundle') if isinstance(include, dict) else include)
        for kind in ('tools', 'hooks', 'providers'):
            for row in value.get(kind, []) or []:
                if isinstance(row, dict) and row.get('module'):
                    source = config.module_sources.get(row['module']) or row.get('source')
                    if source:
                        selected(source)

    for original in config.app_bundles:
        reference = selected(resolve_builtin_behavior(original))
        # Shipped YAML is safe to inspect; an override/local bundle is not a
        # reason to scan its includes or neighboring/unselected cache files.
        parsed = urlsplit(reference)
        if parsed.scheme == 'file':
            sub = parse_qs(parsed.fragment).get('subdirectory', [''])[0]
            path = Path(unquote(parsed.path)) / sub
            if path.parent == resource_root() / 'behaviors' and path.suffix == '.yaml':
                shipped(yaml.safe_load(path.read_text()))
    # Only the source roots that installation actually relocates are equivalent.
    # A user reference elsewhere under app home must keep its exact identity.
    roots = {str(config.home / 'foundation'), str(config.registry_home)}
    if original_home is not None:
        from .updates import foundation_home
        roots.update((str(Path(original_home) / 'foundation'), str(foundation_home(original_home))))
    text = json.dumps([configuration_key(config.settings, *roots),
                       references], sort_keys=True)
    for root in sorted(roots, key=len, reverse=True):
        text = text.replace(root, '$APP')
    return {'sources': list(sources.values()), 'witness': hashlib.sha256(text.encode()).hexdigest()}


def verify_app_selection(manager, rows, *, stage=None):
    """Require the checked selection, including the copied candidate settings."""
    expected = {row.get('appSelection') for row in rows if row.get('kind') != 'smart tool'}
    if not expected:
        return
    if None in expected or expected != {app_selection(manager.home)['witness']}:
        raise ValueError('App behavior selection changed or was not checked; check for updates again.')
    if stage is not None:
        from .host.config import read_config
        config = read_config(stage, home=stage, shared_home=stage/'shared-config', global_only=True)
        if expected != {app_selection(stage, config=config, original_home=manager.home)['witness']}:
            raise ValueError('Staged app behavior selection differs from the checked configuration.')


def classify(home, rows, *, selection=None):
    from .updates import source_key, safe_label
    from .runtime_environment import project_path, receipt_directory
    from .updates import active_release
    packages, sources = included_sources()
    if selection is not None:
        sources = selection['sources']
    required = {source_key(row['url'], row['ref']) for row in sources}
    seen = set()
    for row in rows:
        if row.get('kind') == 'smart tool':
            row['updateTier'] = 'other'
            continue  # A tool package is not an installed conversation dependency.
        try:
            key = source_key(row['url'], row['ref']) if row.get('url') and row.get('ref') else None
        except ValueError:
            key = None  # Local runtime fixtures/overrides have no remote cache identity.
        if key and row.get('kind') == 'bundle / module':
            seen.add(key)  # A package alone cannot supply a declaring source.
        included = (row.get('kind') == 'runtime environment' or row.get('package') in packages or key in required)
        row['updateTier'] = 'included' if included else 'other'
        if selection is not None:
            row['appSelection'] = selection['witness']
    for source in sources:
        key = source_key(source['url'], source['ref'])
        if key in seen:continue
        seen.add(key)
        from .updates import pinned
        rows.append({**source, 'id': 'included:' + hashlib.sha256(str(key).encode()).hexdigest()[:20],
                     'label': safe_label(source['url']), 'kind': 'included source', 'updateTier': 'included',
                     'current': None, 'status': 'pinned' if pinned(source['ref']) else 'not_checked',
                     'eligible': not pinned(source['ref']), 'missing': True,
                     **({'appSelection': selection['witness']} if selection is not None else {}),
                     'usage': 'configured', 'usageEvidence': ['Included with Amplifier Unified']})
    generation = active_release(home).get('current')
    project = project_path(home, generation)
    lock = receipt_directory(home, generation) / 'runtime.lock'
    if packages and not (project / 'uv.lock').exists() and not lock.exists() and not any(row.get('id') == 'runtime:environment' for row in rows):
        rows.insert(0, {'id': 'runtime:environment', 'label': 'Conversation worker environment',
                       'kind': 'runtime environment', 'updateTier': 'included', 'status': 'update',
                       'eligible': True, 'missing': True, 'current': None, 'latest': 'required',
                       **({'appSelection': selection['witness']} if selection is not None else {}),
                       'usage': 'configured', 'usageEvidence': ['Included worker components need installation']})
    return rows


def summary(rows, *, checked=True):
    return {'status': 'available' if any(row.get('status') == 'update' for row in rows) else
            'attention' if any(row.get('status') in {'check_failed', 'local_changes'} for row in rows) else
            'current' if checked else 'waiting',
            'available': sum(row.get('status') == 'update' for row in rows),
            'missing': sum(row.get('status') == 'update' and row.get('missing', False) for row in rows),
            'protected': sum(row.get('status') in {'pinned', 'local'} for row in rows),
            'issues': sum(row.get('status') in {'check_failed', 'local_changes'} for row in rows)}


async def stage_missing(manager, destination, row):
    """Populate a missing shipped source only inside the isolated generation."""
    from .updates import process
    # Populate a conventional staged checkout first. Shared adoption waits
    # until the candidate worker's Foundation API has been checked, so a pinned
    # older worker cannot bypass this checked revision by ignoring a binding.
    from amplifier_foundation.sources.git import GitSourceHandler
    from amplifier_foundation.paths.resolution import parse_uri
    source = await GitSourceHandler()._resolve_locked(parse_uri('git+' + row['url'] + '@' + row['ref']), Path(destination) / 'cache')
    root = Path(source.active_path)
    if not root.resolve().is_relative_to(Path(destination).resolve()):
        raise ValueError('Included source escaped the staging cache')
    actual = await process('git', 'rev-parse', 'HEAD', cwd=root)
    if actual != row['latest']:
        raise ValueError('An included source moved after checking; check for updates again.')
