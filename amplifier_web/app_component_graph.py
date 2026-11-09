"""Resolve new host components, retaining an exact per-installation receipt."""
import asyncio
import hashlib
from importlib import metadata
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from . import app_release_artifacts as artifacts

# Runs with the target environment's isolated interpreter, including older apps.
GRAPH_PROBE = r'''import importlib.metadata as m,json,re
from pathlib import Path
rows=[]
for d in m.distributions():
 row={'name':d.metadata['Name'],'version':d.version,'direct':json.loads(d.read_text('direct_url.json') or 'null')}
 if re.sub(r'[-_.]+','-',row['name'].lower())=='amplifier-unified':
  try:
   with Path(d.locate_file('amplifier_web/release-source.json')).open('rb') as stream: raw=stream.read(16385)
   row['stamp']=json.loads(raw) if len(raw)<=16384 else None
  except (OSError,ValueError): row['stamp']=None
 rows.append(row)
print(json.dumps(rows))
'''


def normalized(rows, *, artifact=None):
    if not isinstance(rows, list) or not rows:
        raise ValueError('The component receipt is empty or malformed')
    result = []
    names = set()
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError('Malformed component receipt')
        name = re.sub(r'[-_.]+', '-', str(raw.get('name', '')).lower())
        version = raw.get('version')
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', name) or name in names:
            raise ValueError('Invalid or duplicate component identity')
        if not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9.!+_-]+', version):
            raise ValueError('Invalid component version')
        names.add(name)
        item = {'name': name, 'version': version}
        direct = raw.get('direct')
        if name == 'amplifier-unified' and artifact is not None:
            context = artifacts.validate_descriptor(artifact, version)
            stamp = artifacts.validate_stamp(raw.get('stamp'), version)
            if (stamp['repository'] != artifacts.REPOSITORY or not isinstance(direct, dict)
                    or set(direct) != {'url', 'archive_info'}
                    or not isinstance(direct['archive_info'], dict)
                    or direct['url'] != Path(context['path']).as_uri()):
                raise ValueError('Installed application does not match its qualified local artifact')
            item.update(url=artifacts.SOURCE, revision=stamp['revision'],
                        delivery={key: context[key] for key in ('url', 'sha256')})
            result.append(item)
            continue
        if direct:
            if not isinstance(direct, dict):
                raise ValueError('Unsupported component source')
            url = direct.get('url', '')
            parsed = urlsplit(url)
            vcs = direct.get('vcs_info', {})
            revision = vcs.get('commit_id', '')
            if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
                parsed.password or parsed.query or parsed.fragment or
                vcs.get('vcs') != 'git' or not re.fullmatch(r'[a-f0-9]{40}', revision)):
                raise ValueError('A component has an unsupported or local source; preserve it and review the installation')
            item.update(url=url, revision=revision)
            subdirectory = direct.get('subdirectory')
            if subdirectory:
                if not isinstance(subdirectory, str) or not re.fullmatch(r'[A-Za-z0-9_/-]+', subdirectory) or any(part in {'', '.', '..'} for part in subdirectory.split('/')):
                    raise ValueError('Invalid component subdirectory')
                item['subdirectory'] = subdirectory
        result.append(item)
    return sorted(result, key=lambda item: item['name'])


def installed_graph():
    rows=[]
    for distribution in metadata.distributions():
        name=re.sub(r'[-_.]+','-',distribution.metadata['Name'].lower())
        if name=='amplifier-unified':
            continue  # installed_target independently verifies the owning app.
        direct=json.loads(distribution.read_text('direct_url.json') or 'null')
        if name.startswith('amplifier-') and direct:
            source=urlsplit(direct.get('url','')) if isinstance(direct,dict) else None
            requested=direct.get('vcs_info',{}).get('requested_revision','') if isinstance(direct,dict) else ''
            if (source is None or source.hostname!='github.com'
                    or not re.fullmatch(r'/microsoft/amplifier[-a-z0-9]*(?:\.git)?',source.path)
                    or (requested not in {'', 'main'} and not re.fullmatch('[a-f0-9]{40}',requested))):
                raise ValueError('An Amplifier component has a custom source; preserve it and review the installation')
        rows.append({'name':name,'version':distribution.version,'direct':direct})
    return normalized(rows)


def digest(graph):
    return hashlib.sha256(json.dumps(graph, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def requirements(graph, *, extras=None):
    """Exact resolver overrides for this candidate, never future updates."""
    lines = []
    for item in graph:
        if item['name'] == 'amplifier-unified':
            continue  # Root app identity is already the selected release commit.
        name = item['name']
        selected = sorted((extras or {}).get(name, ()))
        if any(not re.fullmatch(r'[a-z0-9][a-z0-9-]*', extra) for extra in selected):
            raise ValueError('Invalid component extra')
        if selected:
            name += '[' + ','.join(selected) + ']'
        if item.get('url'):
            value = name + ' @ git+' + item['url'] + '@' + item['revision']
            if item.get('subdirectory'):
                value += '#subdirectory=' + item['subdirectory']
        else:
            value = name + '==' + item['version']
        lines.append(value)
    return '\n'.join(lines) + '\n'


async def read_graph(process, python, *, artifact=None):
    return normalized(json.loads(await process(python, '-I', '-c', GRAPH_PROBE, timeout=30)), artifact=artifact)


def validate_graph(graph):
    if not isinstance(graph, list):
        raise ValueError('Malformed component graph')
    raw = []
    for item in graph:
        if not isinstance(item, dict) or set(item) - {'name', 'version', 'url', 'revision', 'subdirectory', 'delivery'}:
            raise ValueError('Malformed component graph')
        if 'delivery' in item:
            if (set(item) != {'name', 'version', 'url', 'revision', 'delivery'}
                    or item.get('name') != 'amplifier-unified' or item.get('url') != artifacts.SOURCE
                    or not isinstance(item.get('revision'), str)
                    or not re.fullmatch('[a-f0-9]{40}', item['revision'])):
                raise ValueError('Malformed application artifact graph')
            artifacts.validate_delivery(item['delivery'], item['version'])
        direct = None
        if 'url' in item:
            direct = {'url': item['url'], 'vcs_info': {'vcs': 'git', 'commit_id': item.get('revision')}}
            if item.get('subdirectory'):
                direct['subdirectory'] = item['subdirectory']
        elif 'revision' in item or 'subdirectory' in item:
            raise ValueError('Malformed component source')
        raw.append({'name': item.get('name'), 'version': item.get('version'), 'direct': direct})
    result = normalized(raw)
    for original, item in zip(graph, result):
        if 'delivery' in original:
            item['delivery'] = original['delivery']
    if result != graph:
        raise ValueError('Noncanonical component graph')
    return result


async def index_latest(name):
    import aiohttp
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as client:
            async with client.get('https://pypi.org/pypi/' + name + '/json') as response:
                response.raise_for_status()
                value = (await response.json())['info']['version']
                if not re.fullmatch(r'\d+\.\d+\.\d+', value):
                    raise ValueError('Unsupported component release version')
                return value
    except (aiohttp.ClientError, KeyError, TypeError, asyncio.TimeoutError) as error:
        raise ValueError('Component release could not be checked') from error


async def updates(process, environment):
    """Check every installed Amplifier Git dependency, including optional extras."""
    graph = installed_graph()
    rows = [item for item in graph if item['name'].startswith('amplifier-') and item['name'] != 'amplifier-unified']
    tasks = {}
    from .update_checks import git_revision, index_latest as cached_index
    async def remote(url):
        return await git_revision(url, 'main', process, environment)
    async def inspect(item):
        if not item.get('url'):
            latest = await cached_index(item['name'], lambda: index_latest(item['name']))
            current = item['version']
            if not re.fullmatch(r'\d+\.\d+\.\d+', current):
                raise ValueError('A component uses a development version; preserve it and review the installation')
            newer = tuple(map(int, latest.split('.'))) > tuple(map(int, current.split('.')))
            return {**item, 'latest': latest} if newer else None
        parsed = urlsplit(item['url'])
        if parsed.hostname != 'github.com' or not re.fullmatch(r'/microsoft/amplifier[-a-z0-9]*(?:\.git)?', parsed.path):
            raise ValueError('A component uses a custom source; preserve it and review the installation')
        if item['url'] not in tasks:
            tasks[item['url']] = asyncio.create_task(remote(item['url']))
        latest = await tasks[item['url']]
        return {**item, 'latest': latest} if latest != item['revision'] else None
    results = await asyncio.gather(*(inspect(item) for item in rows))
    return [row for row in results if row]
