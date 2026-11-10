"""Prepare the selected independent web client in a new, task-owned directory.

This distribution helper chooses source delivery, not the shipped web client.
The client owns source verification, shared build inputs, and packaging.
"""
from __future__ import annotations

import argparse
import hashlib
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


def copy_tracked(source, destination, *, node='node', development=False):
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
        if development:
            # Authored tests travel only for development/integration preparation.
            # Package installation still uses the bounded production manifest.
            tests = []
            pending = [source / 'tests'] if (source / 'tests').exists() or (source / 'tests').is_symlink() else []
            while pending:
                directory = pending.pop()
                if directory.is_symlink() or not directory.resolve().is_relative_to(source.resolve()):
                    raise ValueError('Expected owned test source directory: ' + str(directory))
                for path in sorted(directory.iterdir()):
                    mode = path.lstat().st_mode
                    if stat.S_ISDIR(mode):
                        pending.append(path)
                    elif stat.S_ISREG(mode):
                        tests.append(path.relative_to(source).as_posix())
                    else:
                        raise ValueError('Expected regular test source: ' + str(path))
            # Source-normalization tests use the selected client's Git rules
            # even when the development source itself has no Git metadata.
            metadata = ['.gitattributes'] if (source / '.gitattributes').exists() else []
            paths = sorted(set(paths) | set(tests) | set(metadata))
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


def development_inventory(source, *, generated_public=()):
    """Local overwrite guard for copied authored files, never release identity."""
    if not isinstance(generated_public, (list, tuple)) or any(not isinstance(name, str) or not name or '/' in name or '\\' in name
           or name in {'.', '..'} for name in generated_public):
        raise ValueError('Expected canonical generated-public member names')
    generated_public = sorted(set(generated_public))
    generated_roots = {'node_modules', 'dist', 'output', '.build-inputs'}
    receipts = {'.client-source.json', '.client-development.json'}
    files = {}

    def visit(directory):
        for path in sorted(directory.iterdir()):
            relative = path.relative_to(source)
            if (len(relative.parts) == 1 and (path.name in generated_roots | receipts
                    or path.name.startswith('.build-inputs-stage-'))
                    or len(relative.parts) == 2 and relative.parts[0] == 'public'
                    and path.name in generated_public):
                continue
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                visit(path)
            elif stat.S_ISREG(mode):
                files[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                raise ValueError('Expected regular prepared source: ' + str(path))

    visit(source)
    return {'schema': 1, 'generatedPublic': generated_public, 'files': files}


def verify_development(source):
    recorded = json.loads((source / '.client-development.json').read_text())
    if (not isinstance(recorded, dict) or recorded.get('schema') != 1
            or not isinstance(recorded.get('generatedPublic'), list)):
        raise ValueError('Unrecognized local prepared-source inventory; retain existing work')
    if recorded != development_inventory(source, generated_public=recorded['generatedPublic']):
        raise ValueError('Edited prepared client source retained; save changes in the client repository before refreshing')


def fetch_source(source, destination):
    command('git', 'init', '-q', destination, capture=True)
    download_command('git', '-C', destination, 'fetch', '--depth=1', '--no-tags',
                     source['repository'] + '.git', source['revision'],
                     env=repository_environment(), timeout=120)
    revision = command('git', '-C', destination, 'rev-parse', 'FETCH_HEAD', capture=True).stdout.strip()
    if revision != source['revision']:
        raise ValueError('Fetched client commit does not match the selection')
    command('git', '-C', destination, 'checkout', '-q', '--detach', revision, capture=True)


def prepare(root, record_path, destination, *, node='node', development=False):
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
        copy_tracked(source, prepared, node=node, development=development)
        # Carry the same trusted selection into a Git-free developer checkout.
        copied_record = prepared / '.client-source.json'
        copied_record.write_text(json.dumps(record, indent=2) + '\n')
        verification = command(node, prepared / 'scripts/client-source.mjs', 'verify', copied_record,
                               capture=True, check=False, timeout=30)
        if verification.returncode:
            raise ValueError('Client verification failed; restore the selected source or refresh '
                             'its record from the corresponding canonical client commit')
        if development:
            generated = command(node, '--input-type=module', '-e',
                'const m=await import(process.argv[1]); console.log(JSON.stringify([...m.generatedPublic]));',
                (prepared / 'scripts/build-identity.mjs').as_uri(), capture=True, timeout=30)
            (prepared / '.client-development.json').write_text(json.dumps(
                development_inventory(prepared, generated_public=json.loads(generated.stdout)),
                sort_keys=True) + '\n')
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
