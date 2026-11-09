"""Explicit additive app features, qualified against the unchanged serving app.

This is updater intent and receipt validation, not a second installer. All
installation, busy admission, replacement and restart remain in app_updates.
"""
import copy
from functools import lru_cache
from importlib import metadata
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

FEATURES = {
    'native-desktop': 'Native screen observation',
    'managed-browser': 'Amplifier browser',
    'decisions-openai': 'OpenAI decisions',
    'decisions-azure': 'Microsoft Decision-1',
    'decisions-jev': 'Jev decisions',
    'decisions-local': 'Local decision service connection',
}


def managed_browser_cache(home):
    """Application-owned browser payloads; never use another app's cache."""
    return Path(home) / 'browsers'


def managed_browser_launch_args():
    """Owner-configured launch flags shared by qualification and live UI use."""
    if os.environ.get('DTU_CHROMIUM_SINGLE_PROCESS') == '1':
        return ['--no-sandbox', '--single-process', '--no-zygote', '--disable-gpu']
    return ['--no-sandbox'] if os.environ.get('AMPLIFIER_UI_BROWSER_NO_SANDBOX') == '1' else []


async def qualify_managed_browser():
    """Explicit installation probe: a fresh blank browser, never user state/sites."""
    from playwright.async_api import async_playwright
    async with async_playwright() as driver:
        owned = Path(driver.chromium.executable_path).resolve()
        if not owned.is_file() or not os.access(owned, os.X_OK):
            raise RuntimeError('The downloaded managed browser binary is unavailable.')
        configured = os.environ.get('AMPLIFIER_UI_BROWSER')
        selected = shutil.which(configured) if configured else str(owned)
        if not selected or not Path(selected).is_file() or not os.access(selected, os.X_OK):
            raise RuntimeError('The explicitly configured browser executable is unavailable.')
        selected = str(Path(selected).resolve())
        args = managed_browser_launch_args()
        browser = await driver.chromium.launch(executable_path=selected, headless=True, args=args, timeout=10000)
        try:
            context = await browser.new_context(viewport={'width': 1280, 'height': 900})
            page = await context.new_page()
            if page.url != 'about:blank':
                raise RuntimeError('Browser qualification did not open an empty page.')
        finally:
            await browser.close()
        return {'ownedExecutable': str(owned), 'selectedExecutable': selected,
                'selection': 'configured' if configured else 'owned',
                'launchArgs': args, 'blankLaunchVerified': True}


@lru_cache(maxsize=8)
def _managed_browser_path(python, cache, version, interval):
    # Public Playwright API in a bounded, isolated helper. It starts no browser
    # and avoids running Playwright's synchronous API on the service event loop.
    script = '''import asyncio
from playwright.async_api import async_playwright
async def main():
 async with async_playwright() as driver:
  print(driver.chromium.executable_path)
asyncio.run(main())
'''
    try:
        result = subprocess.run([python, '-I', '-c', script],
            env={**os.environ, 'PLAYWRIGHT_BROWSERS_PATH': cache},
            capture_output=True, text=True, timeout=3)
        if result.returncode == 0 and len(result.stdout) < 4096:
            path = Path(result.stdout.strip())
            if path.is_absolute() and path.is_relative_to(Path(cache)):
                return str(path)
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def managed_browser_executable(home):
    """Read-only readiness: support package and this host's executable must exist."""
    try:
        version = metadata.version('playwright')
    except metadata.PackageNotFoundError:
        return None
    explicit = os.environ.get('AMPLIFIER_UI_BROWSER')
    path = shutil.which(explicit) if explicit else _managed_browser_path(
        sys.executable, str(managed_browser_cache(home).resolve()), version, int(time.monotonic() // 30))
    return str(Path(path).resolve()) if path and Path(path).is_file() and os.access(path, os.X_OK) else None


def running_application(manager):
    from . import app_updates
    from .app_release_artifacts import distribution_row
    from .update_readiness import artifact_context
    distribution = metadata.distribution('amplifier-unified')
    if Path(distribution.locate_file('amplifier_web')).resolve() != Path(__file__).parent.resolve():
        raise ValueError('Feature installation requires the serving packaged app, not a development checkout.')
    row = app_updates.components.normalized([distribution_row(distribution)], artifact=artifact_context(manager))[0]
    if (row.get('url', '').removesuffix('.git') != app_updates.SOURCE
            or row.get('subdirectory')
            or row.get('revision') != manager.running_identity.get('revision')
            or row['version'] != app_updates.__version__):
        raise ValueError('The serving app must have verified Microsoft source provenance before adding a feature.')
    return {**row, 'url': app_updates.SOURCE}


def status(manager):
    if manager is None:
        return {'supported': False, 'reason': 'Feature installation requires the managed Unified app updater.'}
    from .app_updates import installed_extras, OPTIONAL_EXTRAS
    try:
        app = running_application(manager)
    except (ValueError, OSError, metadata.PackageNotFoundError):
        return {'supported': False, 'reason': 'This host cannot verify its packaged Microsoft app source. Use its deployment owner to install a qualified app before adding optional features.'}
    state = manager.service.state['updates']
    installed = installed_extras()
    return {'supported': True, 'appRevision': app['revision'], 'appVersion': app['version'],
        'installedExtras': installed,
        'pending': bool(manager.lock.locked() or manager.awaiting_restart() or state.get('pendingApp') or state.get('pendingRelease')),
        'action': 'updates.featureInstall', 'feature': 'native-desktop',
        'features': [{'id': key, 'label': label, 'installed': key in installed,
                      **({'providerModule': OPTIONAL_EXTRAS[key][-1].removeprefix('amplifier-module-')}
                         if key.startswith('decisions-') else {}),
                      'requiresConversationUpdate': key.startswith('decisions-')}
                     for key, label in FEATURES.items()],
        'detail': 'Add native observation to this same app revision, preserving installed components and optional features. The app restarts when all work is idle. This installs no desktop-control grant and requests no OS permission.'}


def selection(manager, feature, request_id):
    from .app_updates import installed_extras, validated_extras
    if not isinstance(feature, str) or feature not in FEATURES:
        raise ValueError('Choose a supported optional feature, such as native-desktop or managed-browser.')
    require_provider_support(feature)
    baseline = validated_extras(installed_extras())
    return {'kind': 'add-feature', 'feature': feature, 'requestId': request_id,
        'hostApp': running_application(manager), 'baselineExtras': baseline,
        'extras': validated_extras(sorted(set(baseline) | {feature}))}


def require_provider_support(feature):
    """Adding an extra cannot secretly replace an existing provider revision."""
    if not feature.startswith('decisions-'):
        return
    from .app_updates import OPTIONAL_EXTRAS
    package = OPTIONAL_EXTRAS[feature][-1]
    try:
        distribution = metadata.distribution(package)
    except metadata.PackageNotFoundError:
        return  # A new provider may be installed by this explicit addition.
    if not any(entry.group == 'amplifier.decision_backends'
               for entry in getattr(distribution, 'entry_points', ())):
        raise ValueError('The installed ' + FEATURES[feature] + ' provider predates decision support. '
            'Update that provider in its owning installation first, then retry. '
            'Adding a feature preserves existing component revisions and will not silently upgrade them.')


def validate_selection(manager, selected, extras, *, installing=True):
    from .app_updates import installed_extras, validated_extras
    if (not isinstance(selected, dict) or selected.get('kind') != 'add-feature'
            or selected.get('feature') not in FEATURES
            or not isinstance(selected.get('requestId'), str) or not selected['requestId']):
        raise ValueError('Invalid optional-feature request receipt.')
    baseline = validated_extras(selected.get('baselineExtras'))
    desired = validated_extras(selected.get('extras'))
    if desired != sorted(set(baseline) | {selected['feature']}) or desired != extras:
        raise ValueError('The qualified feature selection changed.')
    if (selected.get('hostApp') != running_application(manager)
            or installing and baseline != validated_extras(installed_extras())):
        raise ValueError('The serving app or its optional features changed after qualification.')
    return desired


def require_preserved_components(candidate, baseline):
    by_name = {row['name']: row for row in candidate}
    if any(by_name.get(row['name']) != row for row in baseline):
        raise ValueError('Adding a feature must preserve every installed component; resolve the dependency conflict before retrying.')


def valid_restart_selection(target):
    """Reject malformed optional restart intent before startup reconciliation."""
    from .app_updates import SOURCE, components, validated_extras
    selected = target.get('featureSelection')
    if selected is None and 'featureSelection' not in target:
        return 'dependencyDigest' not in target
    try:
        if (not isinstance(selected, dict) or selected.get('kind') != 'add-feature'
                or selected.get('feature') not in FEATURES
                or not isinstance(selected.get('requestId'), str) or not selected['requestId']):
            return False
        baseline = validated_extras(selected.get('baselineExtras'))
        if validated_extras(selected.get('extras')) != sorted(set(baseline) | {selected['feature']}):
            return False
        app = components.validate_graph([selected.get('hostApp')])[0]
        return (app['name'] == 'amplifier-unified' and app.get('url') == SOURCE and not app.get('subdirectory')
            and app.get('revision') == target.get('revision') and app['version'] == target.get('version')
            and isinstance(target.get('dependencyDigest'), str)
            and re.fullmatch('[a-f0-9]{64}', target['dependencyDigest']) is not None)
    except (AttributeError, ValueError, TypeError):
        return False


async def record(manager, request_id, phase, *, feature=None, **details):
    async with manager.service.lock:
        rows = manager.service.state['updates'].setdefault('featureResults', {})
        previous = rows.get(request_id, {})
        rows[request_id] = {**previous, 'requestId': request_id,
            'feature': feature or (details.get('selection') or {}).get('feature') or previous.get('feature', 'native-desktop'),
            'phase': phase, 'updatedAt': time.time(), **copy.deepcopy(details)}
        while len(rows) > 20:
            rows.pop(next(iter(rows)))
        manager.service._publish_changes(globals={'updates'})


def reconcile_requests(state):
    """An interrupted qualification is not replayed or marked installed."""
    pending = state.get('pendingReplacement') or state.get('pendingRestart') or state.get('pendingApp') or {}
    selected = pending.get('featureSelection') if isinstance(pending, dict) else None
    request_id = selected.get('requestId') if isinstance(selected, dict) else None
    rows = state.get('featureResults', {})
    if not isinstance(rows, dict):
        state['featureResults'] = {}
        return
    for identity, row in rows.items():
        if not isinstance(row, dict):
            row = rows[identity] = {'requestId':identity, 'feature':'native-desktop', 'phase':'queued'}
        if row.get('phase') in {'queued', 'staging', 'qualified', 'activating', 'restart_pending'}:
            if identity != request_id:
                row.update(phase='interrupted', updatedAt=time.time(),
                    detail='The feature request was interrupted. No installation or restart was replayed; inspect its diagnostic receipt before retrying.')
