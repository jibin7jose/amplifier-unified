"""Seed writable project dependencies after the exact source is installed."""
from __future__ import annotations

import json
import shutil
from pathlib import Path


def initialize(prefix=Path('/opt/unified-prepared'), workspace=Path('/workspace')):
    plan = json.loads((prefix / 'plan.json').read_text())
    if 'executionContract' in plan:
        contract = json.loads((prefix / 'execution-contract.json').read_text())
        if (contract.get('source') != plan['executionContract']['source']
                or contract.get('profileId') != plan['profileId'] or contract.get('version') != 1
                or not (prefix / 'venv/bin/python').is_file()):
            raise ValueError('The prepared execution contract is incomplete or belongs to different source.')
    marker = workspace / '.unified-prepared-dependencies.json'
    if marker.exists():
        if marker.is_symlink() or json.loads(marker.read_text()) != {'preparationId': plan['preparationId']}:
            raise ValueError('The installed dependency preparation changed.')
        return
    if plan['profileId'] == 'browser-test':
        directory = workspace / plan['nodeDirectory']
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError('The project dependency destination changed.')
        target = directory / 'node_modules'
        if target.exists() or target.is_symlink():
            raise ValueError('The source snapshot already supplies project dependency files.')
        shutil.copytree(prefix / 'node' / plan['nodeKey'], target, symlinks=True)
    marker.write_text(json.dumps({'preparationId': plan['preparationId']}))


if __name__ == '__main__':
    initialize()
