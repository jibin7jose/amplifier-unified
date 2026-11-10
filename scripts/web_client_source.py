"""Prepare the selected independent web client in a new, task-owned directory.

This distribution helper chooses source delivery, not the shipped web client.
The client owns source verification, shared build inputs, and packaging.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Reuse the dependency-free repository access and cancellation owner.
from amplifier_web.reset import command, download_command, repository_environment


def selection(path):
    record = json.loads(path.read_text())
    source = record.get('source', {})
    if (record.get('schema') != 1 or record.get('kind') != 'amplifier-web-client-source'
            or source.get('repository') != 'https://github.com/microsoft/amplifier-client-web'
            or not re.fullmatch(r'[a-f0-9]{40}', source.get('revision', ''))
            or not re.fullmatch(r'[a-f0-9]{64}', record.get('inputsSha256', ''))):
        raise ValueError('Select a canonical client source record with an exact commit and input digest')
    delivery = record.get('delivery', {})
    if delivery.get('type') not in {'directory', 'repository'}:
        raise ValueError('Source delivery must be directory or repository')
    return record


def copy_tracked(source, destination, *, node='node'):
    """Copy tracked source, or the client's production manifest in Git-free exports."""
    git = command('git', '-C', source, 'rev-parse', '--show-toplevel',
                  capture=True, check=False)
    if git.returncode == 0:
        paths = command('git', '-C', source, 'ls-files', '--cached', '-z', capture=True).stdout.split('\0')
    else:
        result = command(node, '--input-type=module', '-e',
                         'const m=await import(process.argv[1]); '
                         'console.log(JSON.stringify(m.selectedInputs(process.argv[2])));',
                         (source / 'scripts/client-source.mjs').as_uri(), source,
                         capture=True, timeout=30)
        paths = json.loads(result.stdout)
    destination.mkdir()
    for name in paths:
        if not name:
            continue
        path = source / name
        if not path.resolve().is_relative_to(source.resolve()) or not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError(f'Expected a regular source file: {name}')
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def fetch_source(source, destination):
    command('git', 'init', '-q', destination, capture=True)
    download_command('git', '-C', destination, 'fetch', '--depth=1', '--no-tags',
                     source['repository'] + '.git', source['revision'],
                     env=repository_environment(), timeout=120)
    revision = command('git', '-C', destination, 'rev-parse', 'FETCH_HEAD', capture=True).stdout.strip()
    if revision != source['revision']:
        raise ValueError('Fetched client commit does not match the selection')
    command('git', '-C', destination, 'checkout', '-q', '--detach', revision, capture=True)


def prepare(root, record_path, destination, *, node='node'):
    record = selection(record_path)
    if destination.exists() or destination.is_symlink():
        raise ValueError('Use a new output directory; existing work is never replaced')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.web-client-source-', dir=destination.parent) as temporary:
        stage = Path(temporary)
        delivery = record['delivery']
        if delivery['type'] == 'directory':
            relative = PurePosixPath(delivery.get('path', ''))
            if not delivery.get('path') or relative.is_absolute() or '..' in relative.parts:
                raise ValueError('Directory delivery needs a repository-relative path')
            source = (root / relative).resolve()
            if not source.is_relative_to(root.resolve()):
                raise ValueError('Client source directory must be inside this checkout')
        else:
            source = stage / 'repository'
            fetch_source(record['source'], source)
        prepared = stage / 'source'
        copy_tracked(source, prepared, node=node)
        # Carry the same trusted selection into a Git-free developer checkout.
        copied_record = prepared / '.client-source.json'
        copied_record.write_text(json.dumps(record, indent=2) + '\n')
        verification = command(node, prepared / 'scripts/client-source.mjs', 'verify', copied_record,
                               capture=True, check=False, timeout=30)
        if verification.returncode:
            raise ValueError('Client verification failed; restore the selected source or refresh '
                             'its record from the corresponding canonical client commit')
        prepared.rename(destination)
    return {'directory': str(destination), 'source': record['source'],
            'record': str(destination / '.client-source.json')}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path, help='New directory, outside maintained source')
    parser.add_argument('--selection', type=Path, default=ROOT / 'components/web-client.source.json')
    parser.add_argument('--node', default='node', help='Node executable used by the client verifier')
    args = parser.parse_args(argv)
    try:
        result = prepare(ROOT, args.selection.resolve(), args.destination.absolute(), node=args.node)
    except (OSError, ValueError) as error:
        parser.exit(1, f'{error}\n')
    except subprocess.CalledProcessError:
        parser.exit(1, 'Source preparation failed. Check the selected commit, repository access, '
                    'and Git/Node installation; existing work was preserved.\n')
    except subprocess.TimeoutExpired:
        parser.exit(1, 'Source preparation timed out; existing work was preserved. Retry when ready.\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
