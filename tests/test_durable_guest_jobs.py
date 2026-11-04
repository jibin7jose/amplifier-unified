"""Actual Linux processes; run as root only in an owned test environment.

No container daemon or user workspace is touched. Workload privilege separation
requires uid changes, so other platforms exercise transport/queue tests instead.
"""
import json
import os
from pathlib import Path
import shlex
import signal
import sys
import tempfile
import time

import pytest

from amplifier_web import test_job_guest as guest

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux') or os.geteuid() != 0,
                               reason='Actual supervisor qualification requires isolated Linux root')


@pytest.fixture
def owned(monkeypatch):
    with tempfile.TemporaryDirectory(prefix='unified-guest-job-') as folder:
        root = Path(folder)
        root.chmod(0o755)
        work = root / 'workspace'
        work.mkdir(mode=0o777)
        work.chmod(0o777)
        control = root / 'control'
        temporary = root / 'tmp'
        temporary.mkdir(mode=0o777)
        temporary.chmod(0o777)
        scratch = temporary / 'unified-test'
        source = Path(guest.__file__).read_text().replace("CONTROL = Path('/var/lib/unified-jobs')", 'CONTROL = Path('+repr(str(control))+')')
        source = source.replace("WORKSPACE = '/workspace'", 'WORKSPACE = '+repr(str(work)))
        source = source.replace("SCRATCH = Path('/tmp/unified-test')", 'SCRATCH = Path('+repr(str(scratch))+')')
        monkeypatch.setattr(guest, 'CONTROL', control)
        monkeypatch.setattr(guest, 'WORKSPACE', str(work))
        monkeypatch.setattr(guest, 'SCRATCH', scratch)
        monkeypatch.setattr(guest, 'SCRIPT', source, raising=False)
        yield work, control
        for path in control.glob('*/state.json'):
            state = json.loads(path.read_text())
            for key in ('process', 'supervisor'):
                identity = state.get(key)
                if identity and guest.process_identity(identity['pid']) == identity:
                    try:
                        os.killpg(identity['pid'], signal.SIGKILL)
                    except ProcessLookupError:
                        pass


def payload(command='true', identity='job', root='root', seconds=1200):
    return {'rootOwner': root, 'operationId': 'launch-'+root, 'universeId': 'guest-'+root,
            'jobId': identity, 'command': command, 'timeoutSeconds': seconds, 'environment': {}}


def terminal(value, seconds=8):
    deadline = time.monotonic()+seconds
    while time.monotonic() < deadline:
        result = guest.observe(value)
        if result['job']['terminal']:
            return result
        time.sleep(0.04)
    pytest.fail('The admitted guest did not reach a terminal observation')


def python(code):
    return shlex.join(['/usr/bin/python3', '-c', code])


def test_lost_response_repeated_identity_and_fresh_observer_do_not_replay(owned):
    work, _ = owned
    value = payload(python("import time; open('executions','a').write('once\\n'); time.sleep(.3); print('done')"))
    guest.start(value)  # Deliberately discard the acknowledgement.
    assert guest.start(value)['job']['state'] in {'running', 'passed'}
    result = terminal(value)
    assert result['job']['state'] == 'passed'
    assert result['result']['stdout'] == 'done\n'
    assert (work / 'executions').read_text() == 'once\n'
    assert guest.start(value)['job']['terminal']
    assert (work / 'executions').read_text() == 'once\n'


def test_real_worker_keeps_cache_outside_saved_results_and_recreates_it(owned):
    import shutil
    work, _ = owned
    code = """import json, os
from pathlib import Path
for key in ('HOME', 'XDG_CACHE_HOME'):
    path = Path(os.environ[key]) / 'generated-cache'
    path.write_text('disposable')
    assert path.stat().st_uid == 65534
Path('report.xml').write_text('<testsuite/>')
print(json.dumps({'uid': os.getuid(), 'home': os.environ['HOME'], 'cache': os.environ['XDG_CACHE_HOME']}))
"""
    for index in range(2):
        value = payload(python(code), identity='cache-'+str(index))
        guest.start(value)
        result = terminal(value)
        assert result['job']['state'] == 'passed', result
        observed = json.loads(result['result']['stdout'])
        assert observed['uid'] == 65534
        assert observed['home'] == str(guest.SCRATCH / 'home')
        assert observed['cache'] == str(guest.SCRATCH / 'cache')
        assert sorted(path.name for path in work.iterdir()) == ['report.xml']
        shutil.rmtree(guest.SCRATCH)
        assert (work / 'report.xml').read_text() == '<testsuite/>'


def test_cancel_is_prompt_and_terminal_requires_actual_exit(owned):
    value = payload(python("import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(90)"))
    first = guest.start(value)
    assert not first['job']['terminal']
    time.sleep(.1)
    before = time.monotonic()
    guest.cancel(value)
    result = terminal(value)
    assert time.monotonic()-before < 5
    assert result['job']['state'] == 'cancelled' and result['result']['exit_code'] is not None
    assert guest.process_identity(first['job']['process']['pid']) is None
    assert guest.cancel(value)['job']['state'] == 'cancelled'


def test_timeout_is_enforced_in_guest_and_output_is_bounded(owned):
    value = payload(python("import sys,time; sys.stdout.write('x'*1000000); sys.stdout.flush(); sys.stderr.write('e'*1000000); sys.stderr.flush(); time.sleep(10)"), seconds=1)
    guest.start(value)
    result = terminal(value)
    assert result['job']['state'] == 'timed_out'
    assert result['result']['truncated']
    assert len(result['result']['stdout']) == len(result['result']['stderr']) == guest.OUTPUT_BYTES


def test_worker_cannot_modify_supervisor_receipts_and_profile_environment_survives(owned):
    _, control = owned
    command = python("import os; print(os.getuid(),os.environ['PATH']); "
                     "p="+repr(str(control))+"; print(os.access(p,os.W_OK)); open('owned','w').write('ok')")
    value = payload(command)
    value['environment']['PATH'] = '/prepared/bin:/usr/bin:/bin'
    guest.start(value)
    result = terminal(value)
    assert result['job']['state'] == 'passed'
    assert result['result']['stdout'] == '65534 /prepared/bin:/usr/bin:/bin\nFalse\n'


def test_two_roots_execute_independently_and_cannot_adopt_identity(owned):
    left = payload('sleep .2; printf left', 'left', 'left')
    right = payload('sleep .2; printf right', 'right', 'right')
    guest.start(left)
    guest.start(right)
    with pytest.raises(ValueError, match='another command or owner'):
        guest.start({**left, 'rootOwner': 'other'})
    assert terminal(left)['result']['stdout'] == 'left'
    assert terminal(right)['result']['stdout'] == 'right'


def test_dead_supervisor_is_unknown_and_same_request_never_restarts(owned):
    work, _ = owned
    value = payload(python("import time; open('executions','a').write('once\\n'); time.sleep(15)"))
    first = guest.start(value)
    time.sleep(.1)
    os.kill(first['job']['supervisor']['pid'], signal.SIGKILL)
    time.sleep(.05)
    assert guest.observe(value)['job']['state'] == 'unknown'
    assert guest.start(value)['job']['state'] == 'unknown'
    assert (work / 'executions').read_text() == 'once\n'


def test_crash_after_reservation_does_not_launch_on_observe_or_repeat(owned):
    value = payload('touch forbidden')
    root, expected = guest.directory(value)
    root.mkdir(mode=0o700)
    guest.publish(root / 'admission.json', {'binding': expected})
    assert guest.observe(value)['job']['state'] == 'unknown'
    assert guest.start(value)['job']['state'] == 'unknown'
    assert not (owned[0] / 'forbidden').exists()


def test_background_pipe_inheritance_does_not_hold_finished_check_until_deadline(owned):
    value = payload('sleep 90 & printf done', seconds=1200)
    before = time.monotonic()
    guest.start(value)
    result = terminal(value)
    assert time.monotonic()-before < 5
    assert result['job']['state'] == 'passed'
    assert result['result']['stdout'] == 'done'
