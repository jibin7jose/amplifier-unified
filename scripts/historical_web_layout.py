"""Explicit adapters for qualifying immutable checkouts from older web layouts.

Current development/CI calls the selected-client commands directly. Release tools
carry this small adapter before checking out an older, already selected commit;
an unknown layout is an error, never a request to create a compatibility tree.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


def layout(root):
    root = Path(root)
    selected = (root / 'components/web-client.source.json').is_file()
    builder = (root / 'scripts/web_client_build.py').is_file()
    if (root / 'tests/web/package.json').is_file():
        if selected and builder:
            return 'independent-client'
        raise ValueError('Incomplete independent web-client layout')
    if (root / 'frontend/package.json').is_file():
        if not selected:
            return 'historical-bundled'
        if builder and (root / 'scripts/web_client_development.py').is_file():
            return 'historical-component-view'
    raise ValueError('Unknown selected-checkout web layout')


def environment_directories(root):
    root = Path(root)
    if layout(root) == 'independent-client':
        return root, root / 'tests/web', root / '.build/web-client'
    return root, root / 'frontend', root / 'frontend/.client'


def build_command(root):
    if layout(root) == 'independent-client':
        return [sys.executable, 'scripts/web_client_build.py', 'build']
    return ['npm', 'run', 'build', '--prefix', 'frontend']


def commands(root, operation):
    selected = layout(root)
    if operation == 'prepare':
        if selected == 'independent-client':
            return [[sys.executable, 'scripts/web_client_build.py', 'prepare']]
        if selected == 'historical-component-view':
            return [[sys.executable, 'scripts/web_client_development.py', 'prepare-view']]
        return []
    if operation == 'validate':
        if selected == 'independent-client':
            return [['npm', 'ci', '--prefix', '.build/web-client'],
                    ['npm', 'ci', '--prefix', 'tests/web'],
                    ['npm', 'test', '--prefix', '.build/web-client'],
                    ['npm', 'test', '--prefix', 'tests/web'],
                    build_command(root),
                    ['npm', 'run', 'test:artifact-assets', '--prefix', '.build/web-client']]
        return [['npm', 'ci', '--prefix', 'frontend'],
                ['npm', 'test', '--prefix', 'frontend'], build_command(root)]
    raise ValueError('Unknown historical-layout operation')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('describe', 'prepare', 'validate'))
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    selected = layout(args.root)
    if args.operation == 'describe':
        files = (['tests/web/package-lock.json', 'components/web-client.source.json']
                 if selected == 'independent-client' else ['frontend/package-lock.json'])
        if args.output:
            with args.output.open('a') as stream:
                stream.write('cache-files<<WEB_CLIENT_LAYOUT\n' + '\n'.join(files) + '\nWEB_CLIENT_LAYOUT\n')
        print(json.dumps({'layout': selected, 'cacheFiles': files}))
        return
    for command in commands(args.root, args.operation):
        subprocess.run(command, cwd=args.root, check=True)


if __name__ == '__main__':
    main()
