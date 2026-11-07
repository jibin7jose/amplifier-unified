"""Credential-free update receipts; never retain subprocess text or arguments."""
from __future__ import annotations

import json
import hashlib
import errno
import os
import re
import sqlite3
import time
import uuid

PROBE_PREFIX = 'AMPLIFIER_UPDATE_PROBE='
MAX_ACTIVE_FAILURES = 32
ERROR_TYPES = {'Exception','CommandFailure','CommandTimeout','AssertionError','ImportError','ModuleNotFoundError','FileNotFoundError','PermissionError',
               'OSError','RuntimeError','ValueError','TimeoutError','CancelledError','ModuleActivationError',
               'BundleNotFoundError','BundleLoadError','BundleValidationError','BundleDependencyError','HostComponentConflict','AmbiguousBundleReferenceError',
               'ProtectedRuntimeSource'}
PREPARATION_REASONS = {'ModuleActivationError':'module-prepare-failed','BundleNotFoundError':'bundle-not-found',
                       'BundleLoadError':'bundle-load-failed','BundleValidationError':'bundle-validation-failed',
                       'BundleDependencyError':'bundle-dependency-failed', 'HostComponentConflict':'host-component-conflict'}
STORAGE_REASONS = {getattr(errno, name): reason for name, reason in {
    'ENOSPC': 'disk-full', 'EDQUOT': 'disk-quota-exceeded',
    'EACCES': 'storage-permission-denied', 'EPERM': 'storage-permission-denied',
    'EROFS': 'read-only-storage'}.items() if hasattr(errno, name)}
RECOVERY_REASONS = {'smart-tool-startup-failed', 'smart-tool-update-failed', 'missing-configured-environment', 'incompatible-conversation-loop', 'protected-runtime-source', 'runtime-source-constraint', 'runtime-source-unbound', 'runtime-source-conflict', 'runtime-source-changed', 'runtime-resolution-mismatch', 'runtime-installed-source-mismatch', *PREPARATION_REASONS.values(), *STORAGE_REASONS.values()}
RUNTIME_SOURCE_REASONS = {'protected-runtime-source', 'runtime-source-constraint', 'runtime-source-unbound',
                        'runtime-source-conflict', 'runtime-source-changed', 'runtime-resolution-mismatch',
                        'runtime-installed-source-mismatch'}
RESOLUTION_FIELDS = {'source', 'repository', 'ref', 'subdirectory', 'revision'}
PROBE_STAGES = {'imports','package','assets','login','terminal','complete','prepare','prepared','capabilities','cleanup','worker-imports'}

def sanitized_application_extras(value):
    """Public optional-feature scope; missing or malformed is unknown, not base."""
    if value is None:
        return None
    from .app_updates import validated_extras
    try:
        return validated_extras(value)
    except (TypeError, ValueError):
        return None


def disposition_occurrence(identity, group):
    """Separate from read identity: every occurrence invalidates a close request."""
    eligible = identity == 'updates:failure-overflow' or (
        group.get('kind') == 'application' and
        sanitized_application_extras(group.get('latest', {}).get('applicationExtras')) is None)
    if not eligible:
        return None
    observed = [identity, group.get('id'), group.get('firstAt'), group.get('first'),
                group.get('latest'), group.get('occurrences')]
    return hashlib.sha256(json.dumps(observed, sort_keys=True).encode()).hexdigest()


def exception_type(error):
    name=type(error).__name__
    return name if name in ERROR_TYPES else 'Exception'


def failure_location(value):
    # Code identities only: never filenames, source lines, locals or exception text.
    return isinstance(value, str) and len(value) <= 240 and re.fullmatch(
        r'(?:amplifier_web|amplifier_foundation|amplifier_core)(?:\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*:[0-9]{1,7}', value)


def exception_facts(error):
    """Fixed OS codes and code location; no error messages or file paths."""
    facts = {'errorType': exception_type(error)}
    current, seen = error, set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, OSError) and current.errno in errno.errorcode:
            facts['osError'] = errno.errorcode[current.errno]
            if current.errno in STORAGE_REASONS:
                facts['reason'] = STORAGE_REASONS[current.errno]
        traceback = current.__traceback__
        while traceback:
            frame = traceback.tb_frame
            location = f"{frame.f_globals.get('__name__', '')}:{frame.f_code.co_name}:{traceback.tb_lineno}"
            if failure_location(location):
                facts['failureLocation'] = location
            traceback = traceback.tb_next
        current = current.__cause__ or current.__context__
    return facts


def failure_guidance(event):
    return {
        'runtime-source-unbound': 'A selected component has no matching source binding for its declared ref and commit. The current components remain active. This does not establish local edits. Share the update diagnostics; do not change refs or remove caches to bypass this check.',
        'runtime-source-conflict': 'Selected declarations name conflicting refs for one component repository. The current components remain active. This does not establish local edits. Share the update diagnostics before changing source configuration.',
        'runtime-source-constraint': 'A package source declaration could not be reconciled with the selected component. The current components remain active. This does not establish local edits. Share the update diagnostics for review; do not remove pins or caches to bypass this check.',
        'runtime-installed-source-mismatch': 'A component changed to a different source during update preparation. Amplifier kept the current components active. Check for an app update before retrying; if this happens again, share the update details.',
        'disk-full': 'The update stopped because its destination disk is full. Free space on the computer running Amplifier, then try again.',
        'disk-quota-exceeded': 'The update stopped because its storage quota was reached. Increase the quota or free space, then try again.',
        'storage-permission-denied': 'The update could not access a required file. Review the installation’s file permissions before trying again.',
        'read-only-storage': 'The update destination is read-only. Make the installation writable before trying again.',
    }.get(event.get('reason') or event.get('probe', {}).get('reason'))


def probe_failure(error, stage):
    facts={'ok':False, **exception_facts(error)}
    detail = getattr(error, 'diagnostic_facts', {})
    if detail.get('reason') in RECOVERY_REASONS:
        facts['reason'] = detail['reason']
    if (isinstance(detail.get('package'), str) and len(detail['package']) <= 128
            and re.fullmatch(r'amplifier-[a-z0-9]+(?:-[a-z0-9]+)*', detail['package'])):
        facts['package'] = detail['package']
    from .reset import repository_auth_failure
    current, seen = error, set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        detail = "\n".join(str(value or "") for value in (current, getattr(current, "stderr", None), getattr(current, "stdout", None)))
        if 'github.com' in detail.lower() and repository_auth_failure(detail):
            facts['repositoryAccessFailure'] = True
        current = current.__cause__ or current.__context__
    reason=PREPARATION_REASONS.get(type(error).__name__) if stage=='prepare' else None
    traceback = error.__traceback__
    while traceback:
        frame = traceback.tb_frame
        module = frame.f_globals.get('__name__', '')
        function = frame.f_code.co_name
        location = f'{module}:{function}:{traceback.tb_lineno}'
        if failure_location(location):
            facts['failureLocation'] = location
        if stage == 'prepare' and isinstance(error, ValueError):
            if (module, function) == ('amplifier_web.host.config', 'substitute'):
                reason = 'missing-configured-environment'
            elif (module, function) == ('amplifier_web.host.session', 'live_plan'):
                reason = 'incompatible-conversation-loop'
        traceback = traceback.tb_next
    if reason and 'reason' not in facts:facts['reason']=reason
    return facts


def probe_record(output):
    if isinstance(output,bytes):output=output.decode(errors='replace')
    records=[]
    for line in output.splitlines():
        if not line.startswith(PROBE_PREFIX) or len(line)>8192:continue
        try:value=json.loads(line[len(PROBE_PREFIX):])
        except ValueError:continue
        if not isinstance(value,dict):continue
        safe={key:value[key] for key in ('ok','isolated','packageInEnvironment','frontendPresent','loginAvailable','standalone','providersPresent','cliAbsent','dependenciesPrepared','workerImportsAvailable','repositoryAccessFailure') if type(value.get(key)) is bool}
        if isinstance(value.get('stage'),str) and value['stage'] in PROBE_STAGES:safe['stage']=value['stage']
        if isinstance(value.get('errorType'),str) and value['errorType'] in ERROR_TYPES:safe['errorType']=value['errorType']
        if isinstance(value.get('reason'),str) and value['reason'] in RECOVERY_REASONS:safe['reason']=value['reason']
        if isinstance(value.get('package'),str) and len(value['package']) <= 128 and re.fullmatch(r'amplifier-[a-z0-9]+(?:-[a-z0-9]+)*',value['package']):safe['package']=value['package']
        if failure_location(value.get('failureLocation')):safe['failureLocation']=value['failureLocation']
        if value.get('osError') in errno.errorcode.values():safe['osError']=value['osError']
        for key in ('version','pythonVersion'):
            if isinstance(value.get(key),str) and re.fullmatch(r'\d+\.\d+\.\d+',value[key]):safe[key]=value[key]
        for key in ('elapsedMs', 'sources', 'packages', 'resolverTransactions', 'profileIndex'):
            if type(value.get(key)) is int and value[key]>=0:safe[key]=value[key]
        if 'ok' in safe:records.append(safe)
    return records[-1] if records else None


def command_facts(stdout,stderr,returncode,duration):
    facts={'exitCode':returncode,'durationMs':round(duration*1000),'stdoutBytes':len(stdout),'stderrBytes':len(stderr)}
    probe=probe_record(stdout)
    if probe:facts['probe']=probe
    # Only a fixed exception class, never the following message or traceback.
    classes=re.findall(rb'^([A-Za-z]+Error):',stderr,re.MULTILINE)
    if classes:
        name=classes[-1].decode('ascii')
        if name in ERROR_TYPES:facts['errorType']=name
    return facts


class CommandOutput(str):
    def __new__(cls,value,facts=None):
        result=super().__new__(cls,value);result.diagnostic_facts=facts or {};return result
    def __deepcopy__(self,memo):return str(self)


class CommandFailure(RuntimeError):
    def __init__(self,facts):
        super().__init__('Update command failed; see its sanitized diagnostic receipt')
        self.diagnostic_facts=facts


class CommandTimeout(TimeoutError):
    def __init__(self,duration):
        super().__init__('Update command exceeded its time limit')
        self.diagnostic_facts={'durationMs':round(duration*1000),'errorType':'TimeoutError','timedOut':True}


class UpdateDiagnostics:
    """Small state overview plus bounded private JSONL, with a collector seam."""
    def __init__(self,manager):
        self.manager=manager
        self.state=manager.service.state['updates'].setdefault('diagnostics',{'events':[]})
        self.state.setdefault('events',[])
        self.path=manager.directory/'diagnostics.jsonl'
        # An old lastFailure may be history after exact restart reconciliation.
        if 'activeFailures' not in self.state:
            self.state['activeFailures'] = {}
            updates = manager.service.state['updates']
            failure = self.state.get('lastFailure')
            reconciliation = updates.get('reconciliation') or {}
            recovered = bool(failure and failure.get('kind') == 'application' and failure.get('attemptId') and
                reconciliation.get('attemptId') == failure['attemptId'] and
                reconciliation.get('verifiedAt', 0) >= failure.get('at', 0))
            if failure and not recovered:
                self._track_failure(failure)
        self._pending_failure = None
        from .update_health import retain_preparation
        retain_preparation(self.state)
        # Legacy activeFailures is retained diagnostic history, not another
        # current-health authority. UpdateManager projects verified current
        # desired/active state without rewriting earlier attempt outcomes.

    def _track_failure(self, event):
        phase = event.get('phase', '')
        kind = event.get('kind') or ('application' if phase.startswith(('app-', 'restart-')) else
                                    'ecosystem' if phase.startswith('ecosystem-') else 'update')
        if kind == 'rollback':
            kind = 'ecosystem'
        if kind not in {'application', 'ecosystem'}:
            return  # Other domains retain their existing owner recovery semantics.
        # Attempt/command IDs, elapsed time and byte counts are occurrences, not
        # causes. Retain every original receipt in the existing diagnostic sink.
        cause = {key: event[key] for key in (
            'phase', 'reason', 'component', 'package', 'errorType', 'osError',
            'expectedRevision', 'observedRevision', 'resolutionMismatches',
            'expectedVersion', 'observedVersion', 'failureLocation', 'probe') if key in event}
        if 'probe' in cause:
            cause['probe'] = {key: value for key, value in cause['probe'].items()
                              if key not in {'elapsedMs', 'profileIndex'}}
        cause['tier'] = self.state.get('batch', {}).get('tier')
        if kind == 'application':
            cause['revision'] = event.get('revision')
            cause['applicationExtras'] = sanitized_application_extras(event.get('applicationExtras'))
        if not (event.get('component') or event.get('package') or event.get('probe', {}).get('package')):
            batch = self.state.get('batch', {})
            cause['scope'] = [batch.get('components', []), batch.get('componentCount', 0)]
        fingerprint = hashlib.sha256(json.dumps(cause, sort_keys=True).encode()).hexdigest()
        active = self.state.setdefault('activeFailures', {})
        key = kind + ':' + fingerprint
        previous = active.get(key)
        if previous:
            previous.update(occurrences=previous['occurrences'] + 1, latest=event)
        else:
            if len(active) >= MAX_ACTIVE_FAILURES:
                overflow = self.state.setdefault('failureOverflow', {'occurrences': 0, 'firstAt': event.get('at'), 'first': event})
                overflow.update(occurrences=overflow['occurrences'] + 1, latest=event)
                self._pending_failure = None
                return  # Truthful unresolved overflow; never infer those causes recovered.
            identity = event.get('id') or hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()
            batch = self.state.get('batch', {})
            active[key] = {'id': identity, 'cause': fingerprint, 'kind': kind,
                           'tier': batch.get('tier'), 'components': batch.get('components', []),
                           'componentCount': batch.get('componentCount', 0),
                           'first': event, 'latest': event, 'occurrences': 1}
        self._pending_failure = key

    def _settle_failure(self, phase):
        # Preparation, accepted restart, checks, skips and partial installs are
        # not verified recovery. These completion events already have owner guards.
        kind = {'restart-ack': 'application', 'restart-reconcile': 'application',
                'restart-superseded': 'application'}.get(phase)
        if not kind:
            return
        if self.state.get('kind') not in {kind, 'rollback' if kind == 'ecosystem' else kind}:
            return
        batch = self.state.get('batch', {})
        active = self.state.setdefault('activeFailures', {})
        covered = dict(batch.get('recoveringFailures', {}))
        if phase == 'restart-ack':
            covered.update(batch.get('verifiedBrowserFailures', {}))
            covered.update(batch.get('verifiedStorageFailures', {}))
        for key, observed in covered.items():
            group = active.get(key)
            # Only failures covered when this exact attempt began. A later
            # occurrence invalidates that evidence, including an identical cause.
            if group and group['kind'] == kind and group['latest'].get('id') == observed:
                del active[key]

    def dispose(self, identity, expected_occurrence):
        """Explicit uncertainty acknowledgement, never installation recovery."""
        overflow = identity == 'updates:failure-overflow'
        key = identity.removeprefix('updates:failure:')
        active = self.state.get('activeFailures', {})
        group = self.state.get('failureOverflow') if overflow else active.get(key)
        token = disposition_occurrence(identity, group) if group else None
        if token is None or token != expected_occurrence:
            raise ValueError('Update failure changed or is not eligible; review the current occurrence before closing.')
        from .update_report import event as public_event
        receipt = {'id': uuid.uuid4().hex, 'at': time.time(), 'phase': 'failure-disposition',
                   'status': 'acknowledged-uncertainty', 'recoveryVerified': False,
                   'target': identity, 'occurrence': token, 'occurrences': group['occurrences'],
                   'firstFailure': public_event(group.get('first', {})),
                   'firstFailureAvailable': bool(group.get('first')),
                   'latestFailure': public_event(group['latest'])}
        # Close only this retained episode. Current errors and installation
        # safety gates have separate owners and remain actionable, even when
        # their displayed text happened to match this episode.
        if overflow:
            del self.state['failureOverflow']
        else:
            del active[key]
        self.state['failureDispositions'] = (self.state.get('failureDispositions', []) + [receipt])[-20:]
        self._retain(receipt)
        return receipt

    def capture_activation_retry(self, target, *, rollback=False):
        """Snapshot failures for this validated promotion, before any new effects."""
        recovery = self.state.setdefault('batch', {}).setdefault('recoveringFailures', {})
        for key, group in self.state.get('activeFailures', {}).items():
            event = group['latest']
            if (group['kind'] == 'ecosystem' and event.get('phase') == 'ecosystem-activation'
                    and event.get('revision') == target
                    and (event.get('kind') == 'rollback') == rollback):
                recovery[key] = event.get('id')

    def browser_qualified(self):
        """A validated blank launch covers this attempt's earlier browser failures.

        Qualification alone does not settle attention. The application must still
        complete its guarded activation/restart acknowledgement.
        """
        batch = self.state.get('batch', {})
        if self.state.get('kind') == 'application':
            batch['verifiedBrowserFailures'] = dict(batch.get('browserFailures', {}))

    def browser_failures(self, extras):
        extras = sanitized_application_extras(extras)
        if extras is None or 'managed-browser' not in extras:
            return {}
        result = {}
        for key, group in self.state.get('activeFailures', {}).items():
            failure = group['latest']
            scope = sanitized_application_extras(failure.get('applicationExtras'))
            if (group['kind'] == 'application' and scope is not None and 'managed-browser' in scope
                    and failure.get('phase') in {'managed-browser-install', 'managed-browser-probe'}):
                result[key] = failure.get('id')
        return result

    def begin(self,kind,revision=None,attempt_id=None,*,tier=None,components=(),application_extras=None):
        from .update_health import retain_preparation
        retain_preparation(self.state)
        self._pending_failure = None
        previous_revision = self.state.get('revision')
        identity=attempt_id if isinstance(attempt_id,str) and re.fullmatch(r'[a-f0-9]{32}',attempt_id) else uuid.uuid4().hex
        self.state.update(attemptId=identity,kind=kind)
        if revision and re.fullmatch(r'[a-f0-9]{32,40}',revision):self.state['revision']=revision
        else:self.state.pop('revision',None)
        # Public batch names are repository/package slugs, never arguments,
        # workspaces, credential-bearing URLs or exception messages.
        names=sorted({name for name in components if isinstance(name,str) and
                      re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',name)})
        previous_batch = self.state.get('batch', {})
        # Recovery needs the complete scope. Only the UI preview truncates names.
        self.state['batch']={'attemptId':identity,'kind':kind,'tier':tier if tier in {'included','other'} else kind,
                             'startedAt':time.time(),'components':names,'componentCount':len(names)}
        extras = sanitized_application_extras(application_extras) if kind == 'application' else None
        if extras is not None:
            self.state['batch']['applicationExtras'] = extras
        recovering = {}
        storage_failures = {}
        browser_failures = self.browser_failures(extras) if kind == 'application' else {}
        for key, group in self.state.get('activeFailures', {}).items():
            failure = group['latest']
            if group['kind'] == kind == 'application':
                scope = sanitized_application_extras(failure.get('applicationExtras'))
                matches = bool(revision and failure.get('revision') == revision
                               and scope is not None and extras is not None and scope == extras)
                reason = failure.get('reason') or failure.get('probe', {}).get('reason')
                if (revision and failure.get('revision') and failure.get('revision') != revision
                        and scope is not None and scope == extras and failure.get('id')
                        and reason in STORAGE_REASONS.values()):
                    storage_failures[key] = failure['id']
            elif group['kind'] == kind == 'ecosystem':
                component = failure.get('component') or failure.get('package') or failure.get('probe', {}).get('package')
                scope = set(group.get('components', []))
                matches = (group.get('tier') in {None, 'ecosystem', tier} and
                           (component in names if component else bool(scope) and
                            len(scope) == group.get('componentCount') and scope <= set(names)))
            else:
                matches = False
            if matches:
                recovering[key] = failure.get('id')
        if previous_batch.get('attemptId') == identity and kind != 'application':
            # Re-entering an existing attempt cannot adopt intervening failures.
            recovering = previous_batch.get('recoveringFailures', {})
        self.state['batch']['recoveringFailures'] = recovering
        if kind == 'application':
            same_attempt = (previous_batch.get('attemptId') == identity and previous_revision == revision
                            and previous_batch.get('kind') == kind and previous_batch.get('applicationExtras') == extras)
            # An older updater has no browser snapshot. The successor may take
            # one before its own verified blank launch, never infer that proof.
            self.state['batch']['browserFailures'] = previous_batch.get('browserFailures', browser_failures) if same_attempt else browser_failures
            self.state['batch']['verifiedBrowserFailures'] = previous_batch.get('verifiedBrowserFailures', {}) if same_attempt else {}
            # A newer fully staged app can recover storage failure for the same
            # features. Preserve the pre-stage occurrence through activation;
            # health alone cannot attest a successful installation.
            legacy_storage = {}
            if (same_attempt and extras is not None and 'storageFailures' not in previous_batch
                    and 'verifiedStorageFailures' not in previous_batch):
                # The outgoing updater may predate these snapshots. Its bounded
                # journal must prove this exact stage and the earlier occurrence;
                # activation re-entry changes batch.startedAt, so it is no fence.
                stages = [row for row in self.state['events'] if row.get('kind') == kind
                          and row.get('attemptId') == identity and row.get('phase') == 'stage']
                if ([row.get('status') for row in stages] == ['started', 'succeeded']
                        and all(row.get('revision') == revision
                                and sanitized_application_extras(row.get('applicationExtras')) == extras
                                and type(row.get('at')) in (int, float) for row in stages)
                        and 0 < stages[0]['at'] <= stages[1]['at']):
                    legacy_storage = {key: observed for key, observed in storage_failures.items()
                                      if 0 < self.state['activeFailures'][key]['latest'].get('at', 0) < stages[0]['at']}
            self.state['batch']['storageFailures'] = previous_batch.get('storageFailures', legacy_storage) if same_attempt else storage_failures
            self.state['batch']['verifiedStorageFailures'] = previous_batch.get('verifiedStorageFailures', legacy_storage) if same_attempt else {}
        self.manager.service.state['updates'].pop('probeProgress',None)
        return identity

    def record(self,phase,status,*,preserve_last_failure=False,**facts):
        prior_failure=self.state.get('lastFailure') if preserve_last_failure else None
        event={'id':uuid.uuid4().hex,'at':time.time(),'attemptId':self.state.get('attemptId'),
               'kind':self.state.get('kind'),'phase':phase,'status':status}
        if self.state.get('revision'):event['revision']=self.state['revision']
        if event['kind'] == 'application':
            extras = sanitized_application_extras(self.state.get('batch', {}).get('applicationExtras'))
            if extras is not None:
                event['applicationExtras'] = extras
        if isinstance(facts.get('reason'),str) and facts['reason'] in RECOVERY_REASONS:event['reason']=facts['reason']
        if isinstance(facts.get('resolutionMismatches'), list):
            fields = sorted({value for value in facts['resolutionMismatches']
                             if isinstance(value, str) and value in RESOLUTION_FIELDS})
            if fields:event['resolutionMismatches'] = fields
        for key in ('component', 'installationId', 'targetInstallationId'):
            value = facts.get(key)
            if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', value):event[key] = value
        if isinstance(facts.get('package'),str) and re.fullmatch(r'amplifier-[a-z0-9]+(?:-[a-z0-9]+)*',facts['package']) and len(facts['package'])<=128:
            event['package']=facts['package']
        for key in ('durationMs','exitCode','stdoutBytes','stderrBytes'):
            if type(facts.get(key)) is int:event[key]=facts[key]
        if isinstance(facts.get('errorType'),str) and facts['errorType'] in ERROR_TYPES:event['errorType']=facts['errorType']
        if facts.get('osError') in errno.errorcode.values():event['osError']=facts['osError']
        if failure_location(facts.get('failureLocation')):event['failureLocation']=facts['failureLocation']
        if type(facts.get('timedOut')) is bool:event['timedOut']=facts['timedOut']
        if type(facts.get('anonymousRetry')) is bool:event['anonymousRetry']=facts['anonymousRetry']
        if isinstance(facts.get('commandId'),str) and re.fullmatch(r'[a-f0-9]{32}',facts['commandId']):event['commandId']=facts['commandId']
        for key in ('expectedVersion','observedVersion'):
            if isinstance(facts.get(key),str) and re.fullmatch(r'\d+\.\d+\.\d+',facts[key]):event[key]=facts[key]
        for key in ('expectedRevision','observedRevision'):
            if isinstance(facts.get(key),str) and re.fullmatch(r'[a-f0-9]{40}',facts[key]):event[key]=facts[key]
        if isinstance(facts.get('probe'),dict):
            probe=probe_record(PROBE_PREFIX+json.dumps(facts['probe']))
            if probe:event['probe']=probe
        self.state['events']=(self.state['events']+[event])[-50:]
        self.state['latest']=event
        if event['kind'] == 'application' and phase == 'stage' and status == 'succeeded':
            batch = self.state.get('batch', {})
            batch['verifiedStorageFailures'] = dict(batch.get('storageFailures', {}))
        if status=='succeeded' and phase in {'ecosystem-activation','smart-tools-complete','smart-tools-partial','restart-ack'}:
            batch=self.state.get('batch',{})
            if batch.get('attemptId')==event['attemptId']:
                completed={**batch,'completedAt':event['at'],'phase':phase}
                if event.get('revision'):
                    completed['revision'] = event['revision']
                if phase in {'smart-tools-complete','smart-tools-partial'}:
                    activated=batch.get('activatedComponents',[])
                    completed.update(components=activated[:5],componentCount=len(activated))
                # Smart Tools may activate separately within one attempt.
                prior=[row for row in self.state.get('completedBatches',[]) if row.get('attemptId')!=event['attemptId']]
                self.state['completedBatches']=(prior+[completed])[-8:]
        if status in {'failed','interrupted'}:
            if status=='interrupted' and self.state.get('lastFailure',{}).get('attemptId')==event['attemptId'] and self.state.get('lastFailure',{}).get('status')=='failed':
                prior_failure=self.state['lastFailure']
            self.state['lastFailure']=prior_failure if prior_failure is not None else event
            if prior_failure is None:
                self._track_failure(event)
        elif status == 'succeeded':
            self._settle_failure(phase)
        self._retain(event)
        return event

    def _retain(self, event):
        collector=getattr(self.manager.service,'diagnostics',None)
        if collector:
            try:
                collector.record('updates',{'event':'update:'+event['phase'],'data':event})
            except Exception:pass
        # The general collector may be disabled or retain metadata only. Keep
        # the already-sanitized original in the existing bounded update journal.
        try:
            if self.path.exists() and self.path.stat().st_size>1_000_000:
                old=self.path.with_suffix('.previous.jsonl');os.replace(self.path,old)
            fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
            with os.fdopen(fd,'a') as stream:
                os.fchmod(stream.fileno(),0o600)
                stream.write(json.dumps(event,separators=(',',':'))+'\n')
        except OSError:
            self.state['storageUnavailable']=True
        try:self.manager.service._save_changes(globals={'updates'})
        except (OSError,sqlite3.Error):self.state['storageUnavailable']=True
        return event

    def clear_failure(self):
        self.state.pop('lastFailure',None)
        self.manager.service.state['updates']['error']=None

    def sync(self,phase,operation,*args,**kwargs):
        command_id=uuid.uuid4().hex
        self.record(phase,'started',commandId=command_id)
        started=time.monotonic()
        try:result=operation(*args,**kwargs)
        except Exception as error:
            self.record(phase,'failed',commandId=command_id,**{**exception_facts(error),'durationMs':round((time.monotonic()-started)*1000)})
            raise
        self.record(phase,'succeeded',commandId=command_id,durationMs=round((time.monotonic()-started)*1000))
        return result

    async def run(self,phase,operation,*args,_context=None,**kwargs):
        command_id=uuid.uuid4().hex
        context = _context or {}
        self.record(phase,'started',commandId=command_id,**context)
        started=time.monotonic()
        try:result=await operation(*args,**kwargs)
        except BaseException as error:
            facts={'durationMs':round((time.monotonic()-started)*1000),
                   **getattr(error,'diagnostic_facts',{}), **exception_facts(error)}
            self.record(phase,'interrupted' if type(error).__name__=='CancelledError' else 'failed',commandId=command_id,**{**context,**facts})
            raise
        facts={'durationMs':round((time.monotonic()-started)*1000)}
        facts.update(getattr(result,'diagnostic_facts',{}))
        if isinstance(result,(str,bytes)):
            probe=probe_record(result)
            if probe:facts['probe']=probe
        self.record(phase,'succeeded',commandId=command_id,**{**context,**facts})
        return result
