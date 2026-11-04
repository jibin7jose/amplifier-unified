"""Small allowlisted support facts; never export arbitrary exception payloads."""
import platform
from pathlib import Path
import re


def environment():
    from . import __version__
    return {'applicationVersion': __version__, 'pythonVersion': platform.python_version(),
            'system': platform.system(), 'machine': platform.machine(),
            'kernel': platform.release(), 'wsl': 'microsoft' in platform.release().lower()}


def runtime_versions():
    from importlib.metadata import PackageNotFoundError, version
    result = {}
    for name in ('amplifier-core', 'amplifier-foundation', 'amplifier-module-loop-live',
                 'amplifier-module-provider-openai', 'amplifier-module-provider-anthropic'):
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            pass
    return {'environment': environment(), 'packages': result}


def exception_chain(error):
    import traceback
    chain, seen = [], set()
    while error is not None and id(error) not in seen and len(chain) < 5:
        seen.add(id(error))
        # File basenames/functions/line numbers only: no source code or locals.
        frames = [{'file': Path(frame.filename).name[:160],
                   'line': frame.lineno, 'function': frame.name[:160]}
                  for frame in traceback.extract_tb(error.__traceback__)[-12:]]
        item = {'errorType': type(error).__name__, 'frames': frames}
        if isinstance(error, OSError):
            for key in ('errno', 'winerror'):
                value = getattr(error, key, None)
                if type(value) is int and 0 <= value <= 0xFFFFFFFF:
                    item[key] = value
        from .request_refit import RequestFitChanged
        if isinstance(error, RequestFitChanged) and error.local_refusal:
            item['requestFit'] = {'reason': error.reason, 'components': list(error.components),
                                  **({'cause': error.cause} if error.cause else {})}
        chain.append(item)
        error = error.__cause__ or error.__context__
    return chain


def public_exception_chain(value):
    """Revalidate bounded frames/refusal enums; never accept text or locals."""
    if not isinstance(value, list):
        return []
    result = []
    for item in value[:5]:
        if not isinstance(item, dict):
            continue
        kind = item.get('errorType')
        if not isinstance(kind, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.]{0,99}', kind):
            continue
        frames = item.get('frames')
        safe = []
        for frame in (frames[-12:] if isinstance(frames, list) else []):
            if not isinstance(frame, dict):
                continue
            file, function, line = frame.get('file'), frame.get('function'), frame.get('line')
            if (isinstance(file, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,160}', file)
                    and isinstance(function, str) and re.fullmatch(r'[A-Za-z0-9_.<>]{1,160}', function)
                    and type(line) is int and 0 < line <= 10_000_000):
                safe.append({'file': file, 'function': function, 'line': line})
        row = {'errorType': kind, 'frames': safe}
        for key in ('errno', 'winerror'):
            value = item.get(key)
            if type(value) is int and 0 <= value <= 0xFFFFFFFF:
                row[key] = value
        if kind == 'RequestFitChanged':
            from .request_refit import public_refusal
            refusal = public_refusal(item.get('requestFit'))
            if refusal:
                row['requestFit'] = refusal
        result.append(row)
    return result


def terminal_exception_capture(value):
    """Only correlated generic manager failures may carry supplemental frames."""
    if not isinstance(value, dict):
        return None
    def identity(raw):
        return isinstance(raw, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', raw)
    inputs = value.get('inputIds')
    generation = value.get('generationId')
    kind = value.get('errorType')
    chain = public_exception_chain(value.get('exceptionChain'))
    # loop-live reports this known ValueError subclass as its base type. Keep
    # that original terminal type; only the adapter-certified local subtype
    # can explain it without parsing a flattened exception message.
    local_refit = kind == 'ValueError' and terminal_request_refusal(chain)
    if (not identity(generation) or not isinstance(inputs, list) or not 1 <= len(inputs) <= 128
            or not all(identity(item) for item in inputs)
            or not isinstance(kind, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.]{0,99}', kind)
            or not chain or not (local_refit or any(item['errorType'] == kind for item in chain))):
        return None
    result = {'generationId': generation, 'inputIds': list(inputs),
              'errorType': kind, 'exceptionChain': chain}
    secondary = value.get('secondaryFailure')
    if isinstance(secondary, dict) and secondary.get('stage') == 'loop_exit':
        secondary_chain = public_exception_chain(secondary.get('exceptionChain'))
        if secondary_chain:
            result['secondaryFailure'] = {'stage': 'loop_exit', 'exceptionChain': secondary_chain}
    return result


def terminal_request_refusal(chain):
    """Do not promote an earlier handled refusal over the actual failure."""
    if not chain:
        return None
    index = 1 if chain[0]['errorType'] == 'ManagerTurnError' else 0
    if len(chain) > index and chain[index]['errorType'] == 'RequestFitChanged':
        return chain[index].get('requestFit')
    return None


def event_detail(row):
    result = {}
    for key in ('event', 'timestamp', 'ts', 'at', 'generation_id', 'session_id', 'sessionId',
                'rootSessionId', 'runtimeSessionId', 'request_id', 'response_id', 'tool_call_id', 'provider', 'model',
                'error_type', 'error_stage', 'error_category', 'disposition', 'operation',
                'bridgeId', 'action', 'errorType', 'phase', 'type', 'id', 'turnId', 'parentId', 'kind', 'startedAt', 'endedAt'):
        value = row.get(key)
        if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:/+ -]{1,200}', value):
            result[key] = value
        elif type(value) in (int, float):
            result[key] = value
    for key in ('duration_ms', 'timeoutSeconds', 'elapsedSeconds', 'attempts', 'retryable'):
        if type(row.get(key)) in (int, float, bool):
            result[key] = row[key]
    error = row.get('error')
    if isinstance(error, dict):
        kind = error.get('type')
        if isinstance(kind, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_.]{0,99}', kind):
            result['errorType'] = kind
        message = error.get('msg', error.get('message', ''))
    else:
        message = error
    # Preserve this known application-generated diagnostic verbatim, never an
    # arbitrary SDK message which could embed private prompts or credentials.
    if isinstance(message, str):
        match = re.fullmatch(r'App bridge response timed out after ([0-9.]{1,10}) seconds '
            r'\(transport timeout, not normal coordination.wait expiry\)\.', message)
        if match:
            result['message'] = message
            result['timeoutSeconds'] = float(match[1])
    return result


def execution_detail(row):
    result = event_detail(row)
    failure = row.get('failure')
    if isinstance(failure, dict):
        result['failure'] = {key: failure[key] for key in
            ('category', 'errorType', 'stage', 'code', 'retryable') if key in failure}
        result['failure']['exceptionChain'] = public_exception_chain(failure.get('exceptionChain', []))
    return result
