"""Public GitHub release channel; candidate tool installation is isolated."""
import asyncio
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
import uuid
from importlib import metadata
from . import __version__
from .host.config import write_private
from .updates import process
from . import app_component_graph as components

REPOSITORY='microsoft/amplifier-unified'
SOURCE='https://github.com/'+REPOSITORY
OPTIONAL_EXTRAS={
    'native-desktop': ('amplifier-module-tool-computer-use',),
    'tui': ('amplifier-app-tui',),
    'managed-browser': ('playwright',),
    'decisions-openai': ('amplifier-lib-decisions', 'amplifier-module-provider-openai'),
    'decisions-azure': ('amplifier-lib-decisions', 'amplifier-module-provider-azure-openai'),
    'decisions-jev': ('amplifier-lib-decisions', 'amplifier-module-provider-jev'),
    'decisions-local': ('amplifier-lib-decisions', 'amplifier-module-provider-local-decisions'),
}
PROBE = r'''import json,os,sys
facts={"ok":False,"stage":"imports","isolated":bool(sys.flags.isolated),"pythonVersion":"%s.%s.%s"%sys.version_info[:3]}
try:
 from pathlib import Path
 extras=sys.argv[1:]
 assert len(extras)==len(set(extras)) and all(extra in {"native-desktop","tui","managed-browser","decisions-openai","decisions-azure","decisions-jev","decisions-local"} for extra in extras)
 import amplifier_web
 from amplifier_web import __version__
 from amplifier_web.server import create_app
 import pam
 facts["version"]=__version__
 facts["stage"]="package"
 p=Path(amplifier_web.__file__).resolve().parent
 facts["packageInEnvironment"]=p.is_relative_to(Path(sys.prefix).resolve())
 assert facts["packageInEnvironment"]
 facts["stage"]="assets"
 facts["frontendPresent"]=(p/"static/index.html").is_file()
 assert facts["frontendPresent"]
 from amplifier_ui_use.browser import observation_for
 assert callable(observation_for)
 facts["stage"]="login"
 facts["loginAvailable"]=callable(pam.authenticate)
 assert facts["loginAvailable"]
 if "native-desktop" in extras:
  facts["stage"]="capabilities"
  from amplifier_module_tool_computer_use import foreground
  assert Path(foreground.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
  assert callable(foreground.local_observer) and issubclass(foreground.ForegroundUnavailable,RuntimeError)
 if "tui" in extras:
  import os
  from amplifier_tui.connected import main
  from amplifier_tui.launcher import executable
  facts["stage"]="terminal"
  binary=executable(None)
  assert callable(main) and binary.is_relative_to(Path(sys.prefix).resolve())
  assert binary.is_file() and os.access(binary,os.X_OK)
 if "managed-browser" in extras:
  from playwright.sync_api import sync_playwright
  with sync_playwright() as driver:
   binary=Path(driver.chromium.executable_path)
   assert binary.is_file() and os.access(binary,os.X_OK)
 for extra,module in {"decisions-openai":"amplifier_module_provider_openai.decisions", "decisions-azure":"amplifier_module_provider_azure_openai.decisions", "decisions-jev":"amplifier_module_provider_jev.catalog", "decisions-local":"amplifier_module_provider_local_decisions.catalog"}.items():
  if extra in extras:
   import importlib
   catalog=importlib.import_module(module).catalog()
   assert catalog["schema_version"]==2 and callable(catalog["validate"])
 facts["stage"]="worker-imports"
 import subprocess,tempfile
 worker_probe = """
import importlib.metadata,importlib.util,sys
from pathlib import Path
from types import SimpleNamespace
package=Path(sys.argv[1])
spec=importlib.util.spec_from_file_location("candidate_bootstrap",package/"runtime_bootstrap.py")
bootstrap=importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)
paths=list(sys.path)
web=bootstrap.bootstrap_app_package()
assert bootstrap.bootstrap_app_package() is web
from amplifier_web.collaboration_input import checkpoint_anchors,admit,steer
from amplifier_operations.coordination import fingerprint
import amplifier_operations
assert checkpoint_anchors([],SimpleNamespace(generation=None))==[]
assert callable(admit) and callable(steer) and callable(fingerprint)
assert Path(web.__file__).resolve()==package/"__init__.py"
assert Path(amplifier_operations.__file__).resolve()==package.parent/"amplifier_operations/__init__.py"
assert sys.path==paths and str(package.parent) not in sys.path
assert importlib.util.find_spec("pam") is None
try:
 importlib.metadata.distribution("amplifier-unified")
except importlib.metadata.PackageNotFoundError:
 pass
else:
 raise AssertionError("Host distribution metadata is visible to the worker")
"""
 # A host import can pass while a separately prepared worker cannot load the
 # app's own sibling packages. Use this installed candidate, outside its root,
 # without host site-packages, environment paths, or user-site metadata.
 with tempfile.TemporaryDirectory() as cwd:
  result=subprocess.run([sys.executable,"-I","-S","-c",worker_probe,str(p)],
                        cwd=cwd,capture_output=True,text=True,timeout=15)
 assert result.returncode==0
 facts["workerImportsAvailable"]=True
 facts.update(ok=True,stage="complete")
except Exception as error:
 name=type(error).__name__
 facts["errorType"]=name if name in {"AssertionError","ImportError","ModuleNotFoundError","FileNotFoundError","PermissionError","OSError","RuntimeError","ValueError"} else "Exception"
print("AMPLIFIER_UPDATE_PROBE="+json.dumps(facts),flush=True)
if not facts["ok"]:raise SystemExit(1)
'''


def verified_version(manager,output,expected,phase):
    from .update_diagnostics import probe_record
    report=probe_record(output)
    version=report.get('version') if report and report.get('ok') else output.strip() if report is None else None
    if version_tuple(version)!=version_tuple(expected) or version_tuple(version) is None:
        facts={'errorType':'ValueError'}
        if version_tuple(expected):facts['expectedVersion']=expected.removeprefix('v')
        if version_tuple(version):facts['observedVersion']=version.removeprefix('v')
        if report:facts['probe']=report
        manager.diagnostics.record(phase,'failed',**facts)
        raise ValueError('The probed package does not match the validated release version')
    return version.removeprefix('v')

def version_tuple(value):
    match=re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)',value or '')
    return tuple(map(int,match.groups())) if match else None


def supersedes_staged(release, pending):
    """Only a newer published app may supersede an unactivated app candidate."""
    latest = version_tuple(release.get('latest'))
    staged = version_tuple(pending.get('latest'))
    return bool(not pending.get('featureSelection') and release.get('status') == 'update'
                and release.get('canInstall') is not False and latest and staged and latest > staged
                and re.fullmatch('[a-f0-9]{40}', str(release.get('revision', '')))
                and release['revision'] != pending.get('revision'))


async def check_staged(manager):
    """Refresh availability under the update lock without changing its receipt.

    Check remains read-only. The existing install intent stages any newer target
    separately, and only a successful probe replaces pendingApp. A failed lookup
    leaves the previously qualified target usable, including while offline.
    """
    state = manager.service.state['updates']
    pending = state['pendingApp']
    await manager.publish(applicationCheckPending=True, lastAttempt=time.time())
    try:
        release = await check()
        previous = state.get('application', {})
        if release.get('status') == 'check_failed':
            await manager.publish(applicationCheckWarning='Could not check for a newer release. The prepared update is still available.')
            return
        # A stale mirror/older channel result must not discard a newer known
        # target, downgrade a candidate, or relabel a moved tag as validated.
        known = previous if supersedes_staged(previous, pending) else pending
        if (supersedes_staged(release, known)
                or (release.get('revision') == known.get('revision')
                    and version_tuple(release.get('latest')) == version_tuple(known.get('latest')))):
            release['runningRevision'] = manager.running_identity['revision']
            items = [release if row.get('id') == 'application' else row for row in state.get('items', [])]
            if not any(row.get('id') == 'application' for row in items):
                items.append(release)
            await manager.publish(application=release, items=items, appAvailable=True)
        await manager.publish(lastCheck=time.time(), applicationCheckWarning=None)
    finally:
        await manager.publish(applicationCheckPending=False, pendingAppCheckAt=time.time())

def git_environment():
    from .reset import repository_environment
    return repository_environment()

def installed_extras():
    """Retain known optional features without loading clients or desktop backends."""
    extras=[]
    present={}
    for extra,distributions in sorted(OPTIONAL_EXTRAS.items()):
        for distribution in distributions:
            if distribution not in present:
                try:present[distribution]=metadata.distribution(distribution)
                except metadata.PackageNotFoundError:present[distribution]=None
        if not all(present[name] is not None for name in distributions):continue
        if extra.startswith('decisions-') and not any(
                entry.group == 'amplifier.decision_backends'
                for entry in getattr(present[distributions[-1]], 'entry_points', ())):
            continue
        extras.append(extra)
    return extras


def browser_process_options(manager, extras):
    if 'managed-browser' not in extras:
        return {}
    from .app_features import managed_browser_cache
    return {'env': {**os.environ, 'PLAYWRIGHT_BROWSERS_PATH': str(managed_browser_cache(manager.home))}}


async def prepare_browser(manager, python, extras):
    """Download this package's browser into this app's cache; install no OS packages."""
    if 'managed-browser' not in extras:
        return
    from .app_features import managed_browser_cache, _managed_browser_path
    managed_browser_cache(manager.home).mkdir(parents=True, exist_ok=True, mode=0o700)
    await manager.diagnostics.run('managed-browser-install', process, python, '-I', '-m',
        'playwright', 'install', 'chromium', **browser_process_options(manager, extras), timeout=900)
    receipt = await qualify_browser(manager, python)
    _managed_browser_path.cache_clear()
    return receipt


async def qualify_browser(manager, python):
    probe = '''import asyncio,json
from amplifier_web.app_features import qualify_managed_browser
print(json.dumps(asyncio.run(asyncio.wait_for(qualify_managed_browser(),timeout=12))))
'''
    try:
        output = await manager.diagnostics.run('managed-browser-probe', process, python, '-I', '-c', probe,
            **browser_process_options(manager, ['managed-browser']), timeout=15)
        receipt = json.loads(output)
        if (not isinstance(receipt, dict) or receipt.get('blankLaunchVerified') is not True
                or receipt.get('selection') not in {'configured', 'owned'}
                or not all(isinstance(receipt.get(key), str) and Path(receipt[key]).is_absolute()
                           for key in ('ownedExecutable', 'selectedExecutable'))
                or not isinstance(receipt.get('launchArgs'), list)
                or not all(isinstance(arg, str) for arg in receipt['launchArgs'])):
            raise ValueError('Invalid browser qualification receipt.')
        manager.diagnostics.browser_qualified()
        return receipt
    except (OSError, RuntimeError, TimeoutError, ValueError, TypeError) as error:
        raise ValueError('The browser could not start in this service. Inspect the managed-browser-probe '
            'diagnostic; the service owner may need to provide operating-system libraries or fix the '
            'configured browser executable/launch settings. You can connect an existing browser instead. '
            'No operating-system packages were installed.') from error

def validated_extras(extras):
    if (not isinstance(extras,list) or
        any(not isinstance(extra,str) or extra not in OPTIONAL_EXTRAS for extra in extras) or
        len(extras)!=len(set(extras))):
        raise ValueError('Unsupported optional application features')
    return sorted(extras)

def install_requirement(revision,extras):
    extras=validated_extras(extras)
    source='git+'+SOURCE+'@'+revision
    return 'amplifier-unified['+','.join(extras)+'] @ '+source if extras else source

def application_state():
    from .release_notes import history
    try:
        editable=bool(json.loads(metadata.distribution('amplifier-unified').read_text('direct_url.json') or '{}').get('dir_info',{}).get('editable'))
    except (metadata.PackageNotFoundError,ValueError,TypeError):
        editable=False
    return {'id':'application','label':'Amplifier Unified','kind':'app','current':__version__,'repository':SOURCE,
            'channel':'github-releases','canInstall':not editable,'status':'not_checked','detail':'Check for a published application release.',
            'releaseNotes':history()}

async def github_read(path, *, raw=False, timeout=30):
    """Read public metadata first, even when a signed-in account is restricted."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get('https://api.github.com/' + path,
                headers={'Accept': 'application/vnd.github.raw+json' if raw else 'application/vnd.github+json',
                         'User-Agent': 'amplifier-unified-updates'})
            response.raise_for_status()
            if len(response.content) > 2_000_000:
                raise ValueError('Release metadata is too large')
            return response.text
    except httpx.HTTPError as exc:
        if path == f'repos/{REPOSITORY}/releases/latest' and not raw:
            from .reset import public_release_link
            try:
                return json.dumps(await asyncio.to_thread(public_release_link, timeout))
            except (OSError, ValueError):
                pass
        if os.environ.get('AMPLIFIER_GITHUB_ACCESS') != 'anonymous' and shutil.which('gh'):
            try:
                return await process('gh', 'api', *(['-H', 'Accept: application/vnd.github.raw+json'] if raw else []), path, timeout=timeout)
            except (RuntimeError, TimeoutError, OSError):
                pass
        raise RuntimeError('The release channel could not be reached') from exc


async def check():
    base=application_state()
    try:
        from .update_checks import cached
        async def release_lookup():
            return await github_read(f'repos/{REPOSITORY}/releases/latest')
        data=json.loads(await cached(['release', REPOSITORY], release_lookup))
        tag=data['tag_name'];version=version_tuple(tag)
        if not version or data.get('draft') or data.get('prerelease'):raise ValueError('Unsupported release tag')
        async def tag_lookup():
            return await process('git','ls-remote',SOURCE,'refs/tags/'+tag,'refs/tags/'+tag+'^{}',env=git_environment(),timeout=30)
        output=await cached(['release-tag', SOURCE, tag], tag_lookup)
        rows=[line.split() for line in output.splitlines()]
        revision=next((row[0] for row in rows if row[1].endswith('^{}')),rows[0][0] if rows else '')
        if not re.fullmatch('[0-9a-f]{40}',revision):raise ValueError('Release revision not found')
        current=version_tuple(__version__)
        ahead=version<current
        if version>current:
            # Read only this published, resolved commit; notes on main may describe
            # changes that are not yet installable. Older releases lack this file.
            from .release_notes import parse,history
            try:
                raw=await github_read(f'repos/{REPOSITORY}/contents/amplifier_web/release-notes.json?ref={revision}',raw=True,timeout=15)
                base['releaseNotes']=history(parse(raw,tag.removeprefix('v')),tag)
            except (ValueError,RuntimeError,TimeoutError):
                base['releaseNotesWarning']='Release notes for the available update could not be loaded. Installed release history is still available.'
        # An available app replaces this graph. Do not spend time checking the
        # old host's components against declarations about to be replaced.
        component_updates = await components.updates(process, git_environment()) if version == current else []
        # Never downgrade a development host to an older published application.
        refresh_components = bool(component_updates) and not ahead
        return {**base,'status':'update' if version>current or refresh_components else 'current','latest':tag,'revision':revision,
            'componentUpdates':component_updates,
            'url':SOURCE+'/releases/tag/'+tag,'publishedAt':data.get('published_at'),'releaseBehind':ahead,
            'detail':('This installation is newer than the latest published release. Updates follow published releases, not the main branch.' if ahead
                      else 'A newer application release is available; installation restarts the host when idle.' if version>current
                      else 'Newer application components are available; installation restarts the host when idle.' if refresh_components
                      else 'The latest published application release is installed.')}
    except (ValueError,KeyError,RuntimeError,TimeoutError):
        return {**base,'status':'check_failed','detail':'No accessible published release was found. Check your network connection and release availability.'}

async def stage(manager, *, feature=None, request_id=None):
    from .app_features import selection
    selected = selection(manager, feature, request_id) if feature else None
    revision = selected['hostApp']['revision'] if selected else manager.service.state['updates'].get('application',{}).get('revision')
    extras = validated_extras(selected['extras'] if selected else installed_extras())
    manager.diagnostics.begin('application',revision,application_extras=extras)
    manager.diagnostics.record('stage','started')
    try:await _stage(manager, selected=selected, extras=extras)
    except asyncio.CancelledError:raise
    except Exception:
        phase=manager.diagnostics.state.get('latest',{}).get('phase','stage')
        if manager.diagnostics.state.get('latest',{}).get('status')!='failed':manager.diagnostics.record(phase,'failed',errorType='ValueError')
        await manager.publish(phase='error',error='Application update failed during '+phase.replace('-',' ')+'. The running host was retained.')
        raise


async def _stage(manager, *, selected=None, extras):
    release=({'status':'update','revision':selected['hostApp']['revision'],
              'latest':selected['hostApp']['version'],'featureSelection':selected}
             if selected else manager.service.state['updates'].get('application',{}))
    if release.get('canInstall') is False:
        raise ValueError('This development preview is managed by its owner. Update its checkout and restart it to install a new app version.')
    if release.get('status')!='update':raise ValueError('Check for an application release first')
    revision=release['revision']
    if not re.fullmatch('[0-9a-f]{40}',revision):raise ValueError('Invalid release revision')
    generation=uuid.uuid4().hex
    folder=manager.directory/'applications'/revision/generation
    folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    env={**git_environment(),'UV_TOOL_DIR':str(folder/'tools'),'UV_TOOL_BIN_DIR':str(folder/'bin')}
    uv=shutil.which('uv')
    if not uv:raise ValueError('Install uv before updating the application')
    host_graph=manager.diagnostics.sync('host-components',components.installed_graph)
    resolution_args=['--upgrade','--refresh']
    if selected:
        # This explicit addition must not opportunistically upgrade or omit a
        # pre-existing distribution, including packages beyond app dependencies.
        from .app_features import validate_selection
        from .app_feature_probe import installed_dependency_extras
        validate_selection(manager, selected, extras)
        await manager.diagnostics.run('target-discovery',installed_target)
        dependency_extras=manager.diagnostics.sync('host-dependency-extras',installed_dependency_extras,extras)
        manager.diagnostics.sync('host-components-record',write_private,folder/'host-components.txt',components.requirements(host_graph,extras=dependency_extras))
        resolution_args=['--refresh','--overrides',str(folder/'host-components.txt'),
                         '--with-requirements',str(folder/'host-components.txt')]
    await manager.publish(phase='staging',detail='Installing the app release in an isolated environment…',error=None)
    await manager.diagnostics.run('candidate-install',process,uv,'tool','install','--force',*resolution_args,install_requirement(revision,extras),env=env,timeout=900)
    python=folder/'tools/amplifier-unified'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    browser_receipt = await prepare_browser(manager, python, extras)
    output=await manager.diagnostics.run('candidate-probe',process,python,'-I','-c',PROBE,*extras,**browser_process_options(manager, extras),timeout=30)
    installed=verified_version(manager,output,release['latest'],'candidate-version')
    graph=await manager.diagnostics.run('candidate-components',components.read_graph,process,python)
    app=next((item for item in graph if item['name']=='amplifier-unified'),{})
    if app.get('revision')!=revision or app.get('url')!=SOURCE or app.get('version')!=installed:
        raise ValueError('Candidate component graph does not match the selected application')
    if selected:
        from .app_features import require_preserved_components
        require_preserved_components(graph, host_graph)
        from .app_feature_probe import DEPENDENCY_PROBE
        await manager.diagnostics.run('candidate-dependencies',process,python,'-I','-c',DEPENDENCY_PROBE,*extras,timeout=30)
    manager.diagnostics.sync('candidate-components-record',write_private,folder/'components.txt',components.requirements(graph))
    manager.diagnostics.sync('candidate-record',write_private,folder/'validated.json',json.dumps({'revision':revision,'version':installed,'hostVersion':__version__,'attemptId':manager.diagnostics.state['attemptId'],'extras':extras,'generation':generation,'componentGraph':graph,'componentDigest':components.digest(graph),'hostGraph':host_graph,'hostDigest':components.digest(host_graph),**({'managedBrowser':browser_receipt} if browser_receipt else {}),**({'featureSelection':selected} if selected else {})}))
    manager.diagnostics.clear_failure()
    manager.diagnostics.record('stage','succeeded',observedVersion=installed)
    await manager.publish(phase='app-staged',pendingApp={**release,'attemptId':manager.diagnostics.state['attemptId'],'generation':generation,'extras':extras},detail='Application release validated. Waiting for work to finish before restarting.')
    if selected:
        from .app_features import record
        await record(manager, selected['requestId'], 'qualified', selection=selected,
                     generation=generation, componentDigest=components.digest(graph),
                     detail='Same app and existing components preserved. Waiting for idle guarded activation.')

async def activate(manager, *, force=None):
    async with manager.service.runtime_lifecycle():
        await _activate_serialized(manager, force=force)


async def _activate_serialized(manager, *, force=None):
    if force is not None:
        from .update_restart import require, RestartRejected
        require(manager, force)
        if manager.lock.locked():
            raise RestartRejected('An update operation is already running. Review it before confirming again.')
    if manager.lock.locked() or manager.closed:return
    async with manager.lock:
        state = manager.service.state['updates']
        pending = state.get('pendingApp') or {}
        if force is None and supersedes_staged(state.get('application', {}), pending):
            # A check may finish while activation waits for the runtime owner.
            # Let the existing install intent qualify that newer target first.
            # Explicit force confirmation remains bound to its exact candidate.
            manager.wakeup.set()
            return
        selected = pending.get('featureSelection')
        try:await _activate(manager, force=force)
        except Exception as error:
            from .update_restart import RestartRejected
            if isinstance(error, RestartRejected):
                raise
            if isinstance(selected,dict) and isinstance(selected.get('requestId'),str):
                from .app_features import record
                await record(manager, selected['requestId'], 'error', detail='Feature activation failed validation. No installation was replayed; inspect the update diagnostics.')
            from .update_diagnostics import exception_type
            last=manager.diagnostics.state.get('latest',{})
            if last.get('status')!='failed':
                manager.diagnostics.begin('application',pending.get('revision'),application_extras=pending.get('extras'))
                manager.diagnostics.record('activation-validation','failed',errorType=exception_type(error))
            state=manager.service.state['updates']
            if isinstance(state.get('pendingApp'),dict) and state['pendingApp'].get('generation') and state.get('phase')!='activating':
                # Preserve the failed generation as evidence, but allow a later
                # explicit Install to create a new candidate. Never retry effects.
                await manager.publish(phase='error',pendingApp=None,appAvailable=state.get('application',{}).get('status')=='update' if selected else True,
                    error='The staged application changed or could not be validated. Install again to prepare a new candidate.',
                    detail='The running host and previous candidate evidence were preserved.')
            raise

async def installed_target():
    """Only replace the uv tool environment that owns this running host."""
    uv=shutil.which('uv')
    executable=shutil.which('amplifier-unified')
    if not uv or not executable:
        raise ValueError('Install and launch this app with uv tool before applying application updates')
    env=git_environment()
    tool_root=Path(await process(uv,'tool','dir',env=env,timeout=10)).expanduser().resolve()
    bin_root=Path(await process(uv,'tool','dir','--bin',env=env,timeout=10)).expanduser().resolve()
    prefix=tool_root/'amplifier-unified'
    launcher=prefix/('Scripts/amplifier-unified.exe' if os.name=='nt' else 'bin/amplifier-unified')
    public_launcher=bin_root/('amplifier-unified.exe' if os.name=='nt' else 'amplifier-unified')
    from .application_generations import active, supports
    from .host.config import app_home
    generation = active(app_home())
    owns_prefix = Path(sys.prefix).resolve() == prefix.resolve()
    if generation and supports(prefix):
        owns_prefix = owns_prefix or Path(sys.prefix).resolve() == generation['prefix'].resolve()
    if not owns_prefix or Path(executable).resolve()!=launcher.resolve() or public_launcher.resolve()!=launcher.resolve():
        raise ValueError('This host is not running from the active uv tool installation. Restart it using that installation before updating; no global tool was changed.')
    python=Path(sys.prefix)/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    previous={'version':__version__,'installation':str(prefix),'source':None}
    try:
        info=json.loads(metadata.distribution('amplifier-unified').read_text('direct_url.json') or '{}')
        source=info.get('url')
        if source and info.get('vcs_info',{}).get('commit_id'):
            source='git+'+source+'@'+info['vcs_info']['commit_id']
            if info.get('subdirectory'):source+='#subdirectory='+info['subdirectory']
        previous['source']=source
    except (metadata.PackageNotFoundError,ValueError):
        pass
    return uv,str(public_launcher),python,previous

async def _activate(manager, *, force=None):
    release=manager.service.state['updates'].get('pendingApp')
    if not release:return
    revision=release.get('revision','')
    if not re.fullmatch('[0-9a-f]{40}',revision):raise ValueError('Invalid staged release revision')
    generation=release.get('generation')
    if generation is not None and (not isinstance(generation,str) or not re.fullmatch('[0-9a-f]{32}',generation)):
        raise ValueError('Invalid staged application generation')
    folder=manager.directory/'applications'/revision
    if generation:folder=folder/generation
    marker=folder/'validated.json'
    # Validate one snapshot before closing the runtime or touching the install.
    try:
        validated=json.loads(marker.read_text())
    except (FileNotFoundError,NotADirectoryError,json.JSONDecodeError):
        raise ValueError('App release must pass isolated validation before activation') from None
    if not isinstance(validated,dict) or validated.get('revision')!=revision:
        raise ValueError('App release must pass isolated validation before activation')
    if version_tuple(validated.get('version'))!=version_tuple(release.get('latest')):
        raise ValueError('The pending release does not match its validated package')
    selected=validated.get('featureSelection')
    if selected != release.get('featureSelection') or (selected and not generation):
        raise ValueError('The pending optional feature does not match its qualified generation')
    try:
        extras=validated_extras(validated.get('extras',[]))
        if selected:
            from .app_features import validate_selection
            validate_selection(manager, selected, extras)
            extras_match=True
        else:
            extras_match=extras==installed_extras()
    except ValueError:
        extras_match=False
    if not extras_match:
        message='Optional features changed after validation. Install the update again to validate the current selection.'
        manager.diagnostics.begin('application',revision,validated.get('attemptId') or release.get('attemptId'),
                                  application_extras=validated.get('extras'))
        manager.diagnostics.record('activation-validation','failed',errorType='ValueError')
        # Removing the pending pointer lets the normal install command stage a
        # fresh candidate. Keep its receipt on disk for diagnosis, never reuse it.
        await manager.publish(phase='error',pendingApp=None,appAvailable=manager.service.state['updates'].get('application',{}).get('status')=='update' if selected else True,error=message,detail=message)
        raise ValueError(message)
    graph=None
    if generation:
        graph=components.validate_graph(validated.get('componentGraph'))
        if validated.get('generation')!=generation or components.digest(graph)!=validated.get('componentDigest'):
            raise ValueError('The component receipt does not match this application generation')
        host_graph=components.validate_graph(validated.get('hostGraph'))
        if components.digest(host_graph)!=validated.get('hostDigest') or components.installed_graph()!=host_graph:
            raise ValueError('The installed host components changed after validation; prepare a new candidate')
        if (folder/'components.txt').read_text()!=components.requirements(graph):
            raise ValueError('The component resolution changed after validation')
        if selected:
            from .app_features import require_preserved_components
            require_preserved_components(graph, host_graph)
    if force is None:
        from .update_drain import prepare
        if not await prepare(manager, release):return
    manager.diagnostics.begin('application',revision,validated.get('attemptId') or release.get('attemptId'),
                              application_extras=validated.get('extras'))
    uv,executable,installed_python,previous=await manager.diagnostics.run('target-discovery',installed_target)
    helper_python=marker.parent/'tools/amplifier-unified'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if not helper_python.is_file():
        raise ValueError('The validated app environment is missing; stage the release again')
    if graph is not None:
        candidate_graph=await manager.diagnostics.run('candidate-components-recheck',components.read_graph,process,helper_python)
        if candidate_graph!=graph:
            raise ValueError('The candidate components changed after validation; stage a new update')
    if 'managed-browser' in extras:
        current_browser = await qualify_browser(manager, helper_python)
        if current_browser != validated.get('managedBrowser'):
            raise ValueError('The selected browser executable or launch settings changed after qualification; prepare the update again.')
    if force is not None:
        from .application_generations import supports
        if not generation or not supports(Path(previous['installation'])):
            raise ValueError('Force restart requires a qualified immutable application generation. Wait for work to finish on this installation.')
    async with manager.service.lock:
        if force is not None:
            from .update_restart import require
            require(manager, force)
        if force is None and manager.busy():return
        if selected:
            # Candidate and target probes await external processes. Recheck the
            # serving baseline after those waits, at final activation admission.
            validate_selection(manager, selected, extras)
            if components.installed_graph()!=host_graph:
                raise ValueError('The serving components changed during activation; qualify a new feature candidate')
        replacement = {'version':validated['version'], 'revision':revision,
            'attemptId':manager.diagnostics.state['attemptId'],
            'sourceInstanceId':manager.running_identity['instanceId'], 'requestedAt':time.time()}
        if graph is not None:
            replacement['qualification'] = {'app':next(row for row in graph if row['name']=='amplifier-unified'),
                'extras':extras, 'dependencyDigest':components.digest([row for row in graph if row['name']!='amplifier-unified'])}
        if selected:
            replacement.update(featureSelection=selected, dependencyDigest=replacement['qualification']['dependencyDigest'])
        if force is not None:
            replacement['forceRestart'] = {key: force[key] for key in ('hostInstanceId', 'revision', 'generation', 'attemptId')}
        # Persist this marker before uv can change the active environment. Every
        # error, cancellation and process exit must retain the admission fence.
        if force is None:
            from .update_drain import begin_replacement
            begin_replacement(manager, release)
        manager.service.state['updates'].update(phase='activating',pendingReplacement=replacement,detail='Installing the app update and restarting…')
        manager.service._publish_changes(globals={'updates'})
    try:
        if force is not None:
            # The durable replacement gate precedes interruption. Runtime.close
            # fences preparations, drains owned workers and records shutdown; it
            # never clears checkout uncertainty or resubmits accepted inputs.
            await manager.diagnostics.run('forced-runtime-stop', manager.service.runtime.close)
            runtime = manager.service.runtime
            if (getattr(runtime, 'workers', None) or getattr(runtime, '_preparations', None)
                    or getattr(runtime, 'has_pending_operations', lambda: False)()):
                raise RuntimeError('Runtime shutdown was not confirmed; the update remains fenced.')
        if selected:
            from .app_features import record
            await record(manager, selected['requestId'], 'activating', detail='Installing the qualified same-app feature addition.')
        manager.diagnostics.sync('recovery-record',write_private,manager.directory/'previous-app.json',json.dumps(previous))
        resolution_args=['--overrides',str(folder/'components.txt')] if graph is not None else []
        if selected:
            resolution_args.extend(['--with-requirements',str(folder/'components.txt')])
        from .application_generations import supports, promote
        # Upgrade older bootstraps once through uv. Thereafter this tested app
        # environment is itself the live target: no second install or relocation.
        bootstrap = Path(previous['installation']) if previous.get('installation') else Path(sys.prefix)
        if generation and supports(bootstrap):
            target = manager.diagnostics.sync('replacement-promote', promote, manager.home, validated, bootstrap)
            installed_python = target['prefix'] / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        else:
            await manager.diagnostics.run('replacement-install',process,uv,'tool','install','--force',*resolution_args,install_requirement(release['revision'],extras),env=git_environment(),timeout=900)
        output=await manager.diagnostics.run('replacement-probe',process,installed_python,'-I','-c',PROBE,*extras,**browser_process_options(manager, extras),timeout=30)
        installed=verified_version(manager,output,validated['version'],'replacement-version')
        if graph is not None:
            replacement_graph=await manager.diagnostics.run('replacement-components',components.read_graph,process,installed_python)
            if replacement_graph!=graph:
                raise ValueError('Installed components do not match the qualified application generation')
    except asyncio.CancelledError:
        if selected:
            await record(manager, selected['requestId'], 'interrupted', detail='Installation was interrupted and its outcome is unknown. Work remains paused until the qualified installation is verified on a new host.')
        await manager.publish(phase='interrupted',pendingApp=None,error='Application installation was interrupted. Its files may have changed; work remains paused. Repair the qualified installation and restart before verification.',
                              detail='The replacement outcome is unknown. No installation or conversation work will be replayed.')
        raise
    except Exception as error:
        if selected:
            await record(manager, selected['requestId'], 'error', detail='Feature replacement could not be verified. Its files may have changed; work remains paused until a new host proves the qualified installation.')
        last=manager.diagnostics.state.get('latest',{})
        phase=last.get('phase','activation')
        if last.get('status')!='failed':
            from .update_diagnostics import exception_type
            manager.diagnostics.record(phase,'failed',errorType=exception_type(error))
        await manager.publish(phase='error',pendingApp=None,error='Application update failed during '+phase.replace('-',' ')+'. Its files may have changed; work remains paused. Review the diagnostic receipt and repair the qualified installation before restarting.',
                              detail='The replacement outcome is unknown. Package presence is not verified installation success.')
        return
    # Keep the work gate closed until this process exits. Publishing installed
    # here would permit a new conversation between the helper spawn and SIGTERM.
    manager.diagnostics.clear_failure()
    await manager.publish(phase='activating',pendingApp=None,pendingReplacement=None,appAvailable=False,error=None,
        pendingRestart={'version':validated['version'],'revision':revision,'attemptId':manager.diagnostics.state['attemptId'],
                        'sourceInstanceId':manager.running_identity['instanceId'],'requestedAt':time.time(),
                        **({'forceRestart':replacement['forceRestart']} if 'forceRestart' in replacement else {}),
                        **({'qualification':replacement['qualification']} if 'qualification' in replacement else {}),
                        **({'featureSelection':selected,'dependencyDigest':components.digest([row for row in graph if row['name']!='amplifier-unified'])} if selected else {})},detail='Application installed. Restarting the local host…')
    if selected:
        await record(manager, selected['requestId'], 'restart_pending', detail='Feature installed; awaiting the restarted host and exact component validation.')
    # A generated systemd unit or launchd agent owns its process lifecycle.
    # Asking it to restart avoids racing its restart policy with a second
    # detached process spawned by this in-process updater.
    from .deployment_service import current_process_is_unit_managed
    if current_process_is_unit_managed(manager.home):
        await request_managed_restart(manager)
        return
    # A tiny stdlib helper waits until this host releases its port, then starts
    # the already-verified launcher. It does not execute a shell command.
    options=manager.diagnostics.sync('restart-configuration',restart_arguments,manager,executable)
    helper=manager.directory/'restart.py'
    manager.diagnostics.sync('restart-helper-file',write_private,helper,'''import json,os,subprocess,sys,time
parent=int(sys.argv[1]);args=json.loads(sys.argv[2]);logpath=sys.argv[3]
for _ in range(120):
 try: os.kill(parent,0)
 except OSError: break
 time.sleep(.5)
else: raise SystemExit('Old host did not exit')
with open(logpath,'a') as log:
 os.chmod(logpath,0o600)
 subprocess.Popen(args,start_new_session=True,stdout=log,stderr=log)
''')
    from .host.config import worker_environment
    try:
        await manager.diagnostics.run('restart-helper',asyncio.create_subprocess_exec,str(helper_python),str(helper),str(os.getpid()),json.dumps(options),str(manager.directory/'restart.log'),start_new_session=True,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,env=worker_environment())
    except (OSError,ValueError):
        await manager.publish(phase='activating',error='The update installed, but its restart helper could not start. Restart Amplifier Unified from the terminal.',
                              detail='Waiting for a healthy restarted host. New work remains paused.')
        return
    import signal
    manager.diagnostics.record('restart-request','succeeded')
    os.kill(os.getpid(),signal.SIGTERM)


async def request_managed_restart(manager):
    """Queue an OS-owned restart; only a ready successor confirms completion.

    A service manager can stop this process before its client receives a reply.
    A signal, timeout, or cancelled await therefore leaves the request outcome
    unknown, not successful or rejected. The durable marker and work gate
    survive every outcome of the handoff.
    """
    from .deployment_service import managed_restart_command
    from .update_diagnostics import CommandFailure, exception_type

    marker=dict(manager.service.state['updates']['pendingRestart'])
    marker.update(requestStatus='requesting')
    await manager.publish(phase='activating',pendingRestart=marker)
    command_id=uuid.uuid4().hex
    manager.diagnostics.record('service-restart-request','started',commandId=command_id)
    started=time.monotonic()
    try:
        result=await process(*managed_restart_command(),timeout=30)
    except BaseException as error:
        if not isinstance(error,(Exception,asyncio.CancelledError)):raise
        facts={'durationMs':round((time.monotonic()-started)*1000),'errorType':exception_type(error)}
        facts.update(getattr(error,'diagnostic_facts',{}))
        exit_code=facts.get('exitCode')
        rejected=(isinstance(error,CommandFailure) and type(exit_code) is int and exit_code>0) or (isinstance(error,OSError) and not isinstance(error,TimeoutError))
        status='rejected' if rejected else 'uncertain'
        marker.update(requestStatus=status)
        manager.diagnostics.record('service-restart-request','failed' if rejected else status,commandId=command_id,**facts)
        await manager.publish(phase='activating',pendingRestart=marker,
            error=('The app installed, but the managed restart request was rejected. Run amplifier-unified service restart.' if rejected else None),
            detail=('Waiting for a healthy restarted host. New work remains paused.' if rejected else
                    'Restart request confirmation was interrupted. Waiting for a healthy restarted host; new work remains paused. If it does not reconnect, run amplifier-unified service restart.'))
        if isinstance(error,asyncio.CancelledError):raise
        return
    marker.update(requestStatus='accepted')
    facts={'durationMs':round((time.monotonic()-started)*1000),**getattr(result,'diagnostic_facts',{})}
    manager.diagnostics.record('service-restart-request','accepted',commandId=command_id,**facts)
    await manager.publish(phase='activating',pendingRestart=marker,error=None,
        detail='The service manager accepted the restart request. Waiting for the updated host to become ready…')


def restart_arguments(manager, executable):
    """Preserve effective CLI overrides without rewriting persistent deployment settings."""
    from .deployment import load_server_config, validate_server
    config=getattr(manager.service,'server_config',None)
    config=validate_server(config) if config is not None else load_server_config(manager.home)
    options=[executable,'--no-open','--port',str(manager.service.port),'--data-dir',str(manager.home),
             '--workspace',manager.service.default_workspace,'--session-ttl',str(config['session_ttl_seconds'])]
    for bind in config['bind']:options.extend(['--bind',bind])
    for origin in config['public_origins']:options.extend(['--public-origin',origin])
    if config['tls']['method']!='none':
        for field in ('cert','key'):
            if config['tls'][field]:options.extend(['--tls-'+field,config['tls'][field]])
    return options
