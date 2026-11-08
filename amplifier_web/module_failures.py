"""Allowlisted module-load diagnostics shared by worker, service and UI state."""
import json
from pathlib import Path
import re

from .host.config import write_private

REMEDIATION = {
    'invalid_package_layout': 'Check the module package layout and Python package files.',
    'missing_source': 'Check that the configured module source exists and is available.',
    'invalid_entry_point': 'Check the module entry point and async mount function.',
    'invalid_module_metadata': 'Check the declared module type and metadata.',
    'validation_failed': 'Check the module contract and its required dependencies.',
    'provider_preparation_failed': 'Check this connection’s configured module source and dependencies.',
    'provider_mount_failed': 'Check this connection’s provider startup diagnostic; its provider could not start.',
    'provider_schema_failed': 'Check this provider’s saved endpoint and installed module. Its configuration fields could not be read.',
    'provider_configuration_failed': 'Check this provider’s saved configuration and required credentials.',
    'unknown': 'The module could not be loaded. Check its configuration and dependencies.',
}


def safe_failures(values):
    result=[]
    for row in values[:100] if isinstance(values,list) else []:
        if not isinstance(row,dict):continue
        module=row.get('module',row.get('module_id'))
        if not isinstance(module,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}',module):module='unknown'
        kind=row.get('type',row.get('module_type'))
        if kind not in ('tool','hook','provider','orchestrator','context','resolver'):kind='unknown'
        reason=row.get('reason_code')
        if not isinstance(reason,str) or reason not in REMEDIATION:reason='unknown'
        safe={'module':module,'type':kind,'reason_code':reason,'guidance':REMEDIATION[reason]}
        instance=row.get('instance_id')
        if isinstance(instance,str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}',instance):
            safe['instance_id']=instance
        if kind == 'provider':
            from .provider_failure_diagnostics import guidance, safe_diagnostic
            diagnostic = safe_diagnostic(row.get('diagnostic'))
            if diagnostic is not None:
                safe['diagnostic'] = diagnostic
                safe['guidance'] = guidance(diagnostic) or safe['guidance']
        result.append(safe)
    return result


class ConfiguredModuleError(RuntimeError):
    def __init__(self,failures):
        self.failures=safe_failures(failures)
        super().__init__('Configured modules could not be prepared: '+ '; '.join(
            row['module']+(' ('+row['instance_id']+')' if row.get('instance_id') else '')+': '+row['guidance'] for row in self.failures))


def persist_failures(directory,failures):
    error=ConfiguredModuleError(failures)
    write_private(Path(directory)/'module-load-failures.json',json.dumps(error.failures,indent=2))
    return error


def read_failures(directory):
    try:
        path=Path(directory)/'module-load-failures.json'
        with path.open('rb') as stream:
            content=stream.read(128_001)
        return safe_failures(json.loads(content)) if len(content)<=128_000 else []
    except (OSError,ValueError):
        return []


def clear_failures(directory):
    path=Path(directory)/'module-load-failures.json'
    # A successful preparation also supersedes any older native-side report.
    write_private(path,'[]')
