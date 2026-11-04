"""Pure bounded facts for qualification-only diagnostics; never export raw text.

No file, subprocess or product imports. Callers own IO and mark completeness.
A category is a signature heuristic, not proof of a failure's root cause.
"""
import hashlib
import re


FAILURE_CLASSIFIER_VERSION = 1
FAILURE_SCAN_BYTES = 64 * 1024
FAILURE_SIGNATURES = {
    'manifest': r'^(?:error: |  Caused by: )?(?:TOML parse error|Failed to parse `pyproject\.toml`)',
    'dependency-conflict': r'^[ \t]*(?:error: |\u00d7 )?No solution found when resolving dependencies',
    'transport-auth': r'^(?:fatal: (?:Authentication failed|could not read Username|unable to access)|error: (?:Failed to download|Failed to fetch)|Permission denied \(publickey\))',
    'unavailable-wheel': r'^error: (?:No binary distribution found|No compatible wheel found)',
    'build': r'^[ \t]*(?:error: |\u00d7 )?(?:Failed to build |The build backend returned an error)',
}
CAPTURE_ERRORS = {'log-read-failed', 'log-capture-failed', 'stderr-incomplete', 'stderr-capture-failed'}


def capture_error_facts(returncode, error):
    if error not in CAPTURE_ERRORS:
        raise ValueError('Unknown diagnostic capture error')
    return {'returncode': returncode, 'rawLogSha256': None, 'rawLogBytes': None,
            'rawLogExported': False, 'classifierVersion': FAILURE_CLASSIFIER_VERSION,
            'complete': False, 'captureError': error,
            **({'failureCategory': 'unknown'} if returncode != 0 else {})}


def stream_facts(chunks, returncode, *, complete=True):
    """Hash every supplied byte; retain at most 64KiB+1 for classification.

    Partial input may retain observed-byte identity, but is never classified.
    IO/iteration errors propagate to the caller's guarded capture boundary.
    """
    sha, size, sample = hashlib.sha256(), 0, bytearray()
    for chunk in chunks:
        sha.update(chunk)
        size += len(chunk)
        room = FAILURE_SCAN_BYTES + 1 - len(sample)
        if room > 0:
            sample.extend(chunk[:room])
    facts = {'returncode': returncode, 'rawLogSha256': sha.hexdigest(),
             'rawLogBytes': size, 'rawLogExported': False,
             'classifierVersion': FAILURE_CLASSIFIER_VERSION, 'complete': complete is True,
             'classificationBytes': min(size, FAILURE_SCAN_BYTES),
             'classificationTruncated': size > FAILURE_SCAN_BYTES}
    if returncode == 0:
        return facts
    categories = set()
    if complete is True and type(returncode) is int and returncode > 0 and size <= FAILURE_SCAN_BYTES:
        try:
            text = sample.decode('utf-8')
        except UnicodeDecodeError:
            pass
        else:
            categories = {category for category, pattern in FAILURE_SIGNATURES.items()
                          if re.search(pattern, text, re.MULTILINE | re.IGNORECASE)}
    facts['failureCategory'] = next(iter(categories)) if len(categories) == 1 else 'unknown'
    return facts
