"""Durable admission fence for an installation whose outcome may be unknown."""
import re
import sys


def pending(state):
    return state.get('pendingReplacement') is not None


def qualified(target):
    from .app_updates import SOURCE, components, validated_extras
    try:
        proof = target['qualification']
        app = components.validate_graph([proof['app']])[0]
        validated_extras(proof['extras'])
        return (isinstance(target.get('sourceInstanceId'), str) and bool(target['sourceInstanceId'])
            and app['name'] == 'amplifier-unified' and app.get('url') == SOURCE and not app.get('subdirectory')
            and app['version'] == target['version'] and app.get('revision') == target['revision']
            and isinstance(proof.get('dependencyDigest'), str)
            and re.fullmatch('[a-f0-9]{64}', proof['dependencyDigest']) is not None)
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def matches_running(manager, target):
    """Package presence alone never clears an uncertain replacement."""
    from .app_features import running_application
    from .app_updates import components, installed_extras
    if not qualified(target):
        return False
    proof = target['qualification']
    return (running_application(manager) == proof['app']
        and installed_extras() == proof['extras']
        and components.digest(components.installed_graph()) == proof['dependencyDigest'])


async def verify_running(manager, target):
    """Repeat package/import verification without installing or observing."""
    from . import app_updates
    from .update_diagnostics import probe_record
    if not matches_running(manager, target):
        return False
    manager.diagnostics.begin('application', target['revision'], target['attemptId'],
                              application_extras=target['qualification']['extras'])
    output = await manager.diagnostics.run('replacement-recovery-probe', app_updates.process,
        sys.executable, '-I', '-c', app_updates.PROBE, *target['qualification']['extras'], timeout=30)
    report = probe_record(output)
    if (not report or report.get('ok') is not True or report.get('isolated') is not True
            or report.get('stage') != 'complete'):
        manager.diagnostics.record('replacement-recovery-probe', 'failed', errorType='ValueError')
        return False
    app_updates.verified_version(manager, output, target['version'], 'replacement-recovery-version')
    return True
