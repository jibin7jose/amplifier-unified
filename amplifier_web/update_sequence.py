"""App-first update phases and the included component inventory.

Only read shipped declarations here. Checking must never compose user bundles,
install packages, or activate any cached source.
"""
from pathlib import Path
import hashlib
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


def classify(home, rows):
    from .updates import source_key, safe_label
    from .runtime_environment import project_path, receipt_directory
    from .updates import active_release
    packages, sources = included_sources()
    required = {source_key(row['url'], row['ref']) for row in sources}
    seen = set()
    required_urls = {key[0] for key in required}
    for row in rows:
        if row.get('kind') == 'smart tool':
            row['updateTier'] = 'other'
            continue  # A tool package is not an installed conversation dependency.
        try:
            key = source_key(row['url'], row['ref']) if row.get('url') and row.get('ref') else None
        except ValueError:
            key = None  # Local runtime fixtures/overrides have no remote cache identity.
        if key:seen.add(key[0])
        included = (row.get('kind') == 'runtime environment' or row.get('package') in packages or (key and key[0] in required_urls))
        row['updateTier'] = 'included' if included else 'other'
    for source in sources:
        key = source_key(source['url'], source['ref'])
        if key[0] in seen:continue
        rows.append({**source, 'id': 'included:' + hashlib.sha256(str(key).encode()).hexdigest()[:20],
                     'label': safe_label(source['url']), 'kind': 'included source', 'updateTier': 'included',
                     'current': None, 'status': 'not_checked', 'eligible': True, 'missing': True,
                     'usage': 'configured', 'usageEvidence': ['Included with Amplifier Unified']})
    generation = active_release(home).get('current')
    project = project_path(home, generation)
    lock = receipt_directory(home, generation) / 'runtime.lock'
    if packages and not (project / 'uv.lock').exists() and not lock.exists() and not any(row.get('id') == 'runtime:environment' for row in rows):
        rows.insert(0, {'id': 'runtime:environment', 'label': 'Conversation worker environment',
                       'kind': 'runtime environment', 'updateTier': 'included', 'status': 'update',
                       'eligible': True, 'missing': True, 'current': None, 'latest': 'required',
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
