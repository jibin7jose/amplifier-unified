#!/usr/bin/env python3
"""Opt-in native Docker qualification through the maintained setup/update owners.

Run with an installed candidate's Python, an explicitly task-private data root,
exact core commit and observed local daemon ID. This never installs a daemon or
edits a live app's configuration. Keep the directory after failure/UNKNOWN and
resume this same command; never substitute a fresh root to retry an effect.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from types import SimpleNamespace

from filelock import FileLock, Timeout

from amplifier_web import managed_runner_updates as updates
from amplifier_web import managed_runner_qualification as qualification
from amplifier_web import task_environments as environments
from amplifier_web import test_environment_setup as setup
from amplifier_web.smart_tools import SmartToolsManager


def private_home(home):
    home = home.absolute()
    if any(part.is_symlink() for part in (home, *home.parents)):
        raise ValueError('The private qualification home must not contain symlinks.')
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = home.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('The qualification home must be a private owned directory.')
    return home


async def stage_once(app, commit):
    path = app.data_dir / 'qualification-update.json'
    attempt = setup.read_json(path)
    if attempt is None:
        original = updates.raw_configuration(app)
        source = updates.update_sources(app)[0]
        assert source['eligible'] and source['runnerPolicy']['origin'] == 'app-setup'
        source.update(latest=commit, status='update')
        attempt = {'runnerUpdate': updates.intent_id(source), 'source': source,
                   'previous': original,
                   'previousNormalized': environments.validate_configuration_value(app, original)[0]}
        # Dispatch intent precedes installation; a lost return cannot reinstall.
        setup.write_json(path, attempt)
        staged = await updates.stage(app, source)
        assert staged['runnerUpdate'] == attempt['runnerUpdate']
        assert updates.raw_configuration(app) == original, 'Staging selected an unqualified candidate'
    record = setup.read_json(updates.record_path(app, attempt['runnerUpdate']))
    if not record or not all(record.get(key) for key in ('candidate', 'normalized', 'runtime')):
        raise ValueError('Original staging is incomplete; retain its installation receipt without replay.')
    if record['previous'] != attempt['previous'] or record['revision'] != commit:
        raise ValueError('The retained qualification staging identity changed.')
    return attempt


async def qualify(home, commit, daemon, wait_seconds):
    home = private_home(home)
    lock = FileLock(str(home / 'qualification.lock'), timeout=0, mode=0o600)
    try:
        lock.acquire()
    except Timeout as exc:
        raise ValueError('Qualification is already running for this private home.') from exc
    try:
        # One process owns initialization, stage/advance and final persistence.
        # Never unlink the lock: later callers must lock the same inode.
        return await qualify_locked(home, commit, daemon, wait_seconds)
    finally:
        lock.release()


def verify_target(config, choice, daemon):
    if (config.get('backend', 'docker') != 'docker'
            or config.get('dockerHost') != choice['dockerHost']):
        raise ValueError('The actual setup/qualification target differs from the expected Docker target.')
    observed = json.loads(setup.command([choice['executable'], '--host', config['dockerHost'],
                                        'info', '--format', '{{json .}}']))
    if observed['ID'] != daemon:
        raise ValueError('The explicitly expected local daemon identity changed.')


def verify_setup(record, choice, daemon):
    verify_target(record.get('candidateConfiguration', {}), choice, daemon)
    proof = record.get('proof', {})
    if proof.get('backend') != 'docker' or proof.get('daemon') != daemon:
        raise ValueError('The retained setup proof differs from the expected Docker daemon.')


@contextmanager
def bound_setup(choice, daemon):
    """Add driver assertions at actual selection; never replace native effects."""
    original = setup.setup_docker
    async def checked(base, selected, owned, record, step):
        verify_target(selected, choice, daemon)
        async def checked_step(*args):
            await step(*args)
            verify_target(selected, choice, daemon)
        config, proof = await original(base, selected, owned, record, checked_step)
        verify_setup({'candidateConfiguration': config, 'proof': proof}, choice, daemon)
        return config, proof
    setup.setup_docker = checked
    try:
        yield
    finally:
        setup.setup_docker = original


def verify_update(app, identity, choice, daemon):
    record = setup.read_json(updates.record_path(app, identity))
    if not record:
        raise ValueError('The original qualification record is missing.')
    verify_target(record['candidate'], choice, daemon)
    verify_target(record['normalized'], choice, daemon)
    expected = {'backend': 'docker', 'daemon': daemon}
    if record.get('resourceId'):
        row = environments.get(app.data_dir, qualification.owner(record), record['resourceId'])
        # Before prepare there is no physical binding. Afterwards it must be
        # the expected daemon, before capacity/start or any subsequent effect.
        if row.get('backendIdentity') is not None and row['backendIdentity'] != expected:
            raise ValueError('The prepared qualification owner belongs to another Docker daemon.')
        if record.get('probeState') not in {'new', 'prepare'} and row.get('backendIdentity') != expected:
            raise ValueError('The prepared qualification owner has no expected daemon identity.')
    if record.get('proof') and record['proof'].get('backendIdentity') != expected:
        raise ValueError('The final qualification proof belongs to another Docker daemon.')
    return record


async def qualify_locked(home, commit, daemon, wait_seconds):
    intent = {'version': 1, 'commit': commit, 'daemon': daemon, 'owner': 'native-runner-qualification'}
    path = home / 'qualification-intent.json'
    prior = setup.read_json(path)
    if prior is not None and prior != intent:
        raise ValueError('This private qualification directory belongs to another intent.')
    if prior is None and any(item.name != 'qualification.lock' for item in home.iterdir()):
        raise ValueError('Initial qualification needs an empty task-private directory, never a live app home.')
    setup.write_json(path, intent)
    choice = next(row for row in setup.discover() if row['id'] == 'docker')
    if not choice['available']:
        raise ValueError(choice['reason'])
    verify_target(choice, choice, daemon)
    state_path = home / 'fixture-state.json'
    app = SimpleNamespace(data_dir=home, state=setup.read_json(state_path) or {'settings': {}},
                          lock=asyncio.Lock(), db=sqlite3.connect(home / 'fixture.sqlite3'))
    def publish(**kwargs):
        setup.write_json(state_path, app.state)
        app.db.commit()
    app._publish_changes = publish
    app.smart_tools = SmartToolsManager(app)
    report = {'intent': intent, 'module': environments.__file__, 'result': 'incomplete'}
    try:
        request = {'workspaceId': 'native-qualification', 'sessionId': 'native-qualification',
                   'requestId': 'bootstrap-v1'}  # Omitted backend must select Docker.
        existing = setup.read_json(setup.root(app) / 'latest.json')
        if existing:
            record = setup.read_json(setup.root(app) / (existing['id'] + '.json'))
            if record['state'] != 'completed':
                raise ValueError('Original setup is not completed; inspect its exact receipt, do not replay it.')
        else:
            with bound_setup(choice, daemon):
                await setup.request(app, request, {'sessionId': 'native-qualification'})
                try:
                    await asyncio.gather(*list(app._test_setup_tasks.values()))
                finally:
                    # Settle owned tasks before releasing the driver wrapper/lock.
                    await setup.close(app)
            latest = setup.read_json(setup.root(app) / 'latest.json')
            record = setup.read_json(setup.root(app) / (latest['id'] + '.json'))
            if record['state'] != 'completed':
                raise ValueError('Setup did not complete: ' + record.get('detail', record['state']))
        verify_setup(record, choice, daemon)
        verify_target(updates.raw_configuration(app), choice, daemon)
        report['setup'] = setup.public(record)
        attempt = await stage_once(app, commit)
        original, original_normalized = attempt['previous'], attempt['previousNormalized']
        identity = attempt['runnerUpdate']
        deadline = time.monotonic() + wait_seconds
        while True:
            verify_update(app, identity, choice, daemon)
            result = await updates.advance(app, identity)
            retained = verify_update(app, identity, choice, daemon)
            print(json.dumps({'updateId': identity, 'phase': retained.get('probeState'), **result}), flush=True)
            if result['complete']:
                break
            if result.get('waiting') == 'unknown' or time.monotonic() >= deadline:
                raise ValueError('Qualification retained its original owner: ' + json.dumps(result))
            await asyncio.sleep(1)
        selected = updates.raw_configuration(app)
        verify_target(selected, choice, daemon)
        assert selected['commit'] == commit and selected['runnerQualificationRequired'] is True
        assert updates.trusted_installation(app, selected)
        normalized = environments.validate_configuration_value(app, selected)[0]
        updates.validate_execution(home, normalized)
        assert environments.validate_configuration_value(app, original)[0] == original_normalized
        row = environments.get(home, qualification.owner(retained), retained['resourceId'])
        assert row['status'] == 'retired' and row['environmentState'] == 'removed'
        # Re-observe the terminal job and absence without executing the command again.
        assert qualification.observe(app, row, normalized)['status'] == 'absent'
        assert retained['check']['job']['terminal'] and retained['check']['result']['exit_code'] == 0
        assert retained['proof']['artifactVerified'] and retained['proof']['probeRemoved']
        assert (await updates.advance(app, identity))['complete'], 'Lost selection acknowledgement did not reconcile'
        verify_update(app, identity, choice, daemon)
        report.update(result='qualified', updateId=identity, selected=selected, runtime=retained['runtime'],
                      proof=retained['proof'], check=retained['check'], owner=row['sessionId'],
                      universeId=row['universeId'], oldBindingPreserved=True,
                      effects={name: value['requestId'] for name, value in retained['effects'].items()})
    except BaseException as error:
        report.update(errorType=type(error).__name__, error=str(error))
        raise
    finally:
        report['sourceSha256'] = hashlib.sha256(Path(environments.__file__).read_bytes()).hexdigest()
        setup.write_json(home / 'qualification-result.json', report)
        await app.smart_tools.close()
        app.db.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-home', type=Path, required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--expected-daemon', required=True)
    parser.add_argument('--wait-seconds', type=int, default=180)
    args = parser.parse_args()
    if not re.fullmatch('[a-f0-9]{40}', args.commit):
        parser.error('Use an exact reviewed 40-character core commit.')
    if not args.expected_daemon or args.wait_seconds < 1:
        parser.error('An observed daemon and positive bounded wait are required.')
    os.umask(0o077)
    print(json.dumps(asyncio.run(qualify(args.private_home, args.commit,
                                      args.expected_daemon, args.wait_seconds)), sort_keys=True))


if __name__ == '__main__':
    main()
