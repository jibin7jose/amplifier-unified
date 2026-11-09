"""No-model compatibility probe in a staged environment, never a user session."""
import asyncio
import argparse
from pathlib import Path
import importlib.util
import json
import sys
import time
if __package__:
    from .runtime_bootstrap import bootstrap_app_package
else:
    from runtime_bootstrap import bootstrap_app_package
bootstrap_app_package()
from amplifier_web.update_diagnostics import exception_type,probe_failure,PROBE_PREFIX

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace")
    parser.add_argument("bundle")
    parser.add_argument("--refresh-dependencies", action="store_true")
    parser.add_argument("--install-overrides", type=Path)
    parser.add_argument("--read-only", action="store_true")
    parser.add_argument("--profiles", type=Path)
    parser.add_argument("--source-policy-only", type=Path)
    parser.add_argument("--checked-sources", type=Path)
    parser.add_argument("--global-only", action="store_true")
    parser.add_argument("--runtime-plan", type=Path)
    args = parser.parse_args()
    runtime_plan = json.loads(args.runtime_plan.read_text()) if args.runtime_plan else None
    started = time.monotonic()
    async def deny(*args): return 'deny'
    facts={'ok':False,'stage':'prepare'}
    session=None
    try:
        if args.profiles:
            from amplifier_foundation.modules.batch import DependencyBatch
            from amplifier_web.host.session import prepare_dependencies
            batch = DependencyBatch()
            profiles = json.loads(args.profiles.read_text())
            catalog = {}
            from amplifier_web.runtime_policy_reconciliation import Collector
            collector = Collector() if args.source_policy_only or args.checked_sources else None
            if not isinstance(profiles, list) or not profiles or any(not isinstance(p, str) for p in profiles):
                raise ValueError('Invalid qualification profiles')
            for index, profile in enumerate(profiles):
                facts['profileIndex'] = index + 1
                await prepare_dependencies(args.workspace, bundle=profile,
                    install_overrides=args.install_overrides, dependency_batch=batch, global_only=True, runtime_plan=runtime_plan,
                    profile_catalog=catalog, source_policy_collector=collector)
            facts.pop('profileIndex', None)
            from amplifier_web.host.config import load_config
            if args.source_policy_only:
                from amplifier_web.deployment import write_private
                config = load_config(args.workspace, global_only=True)
                evidence = await collector.report(batch, config, allow_generated=True)
                write_private(args.source_policy_only, json.dumps(evidence), owned_root=config.home)
                facts.update(ok=True, stage='source-policy-collected', dependenciesPrepared=False)
                print(PROBE_PREFIX + json.dumps(facts), flush=True)
                return
            if args.checked_sources:
                from amplifier_web.runtime_source_alignment import checked_batch_sources, verify_checked_selection
                checked = json.loads(args.checked_sources.read_text())
                selection = await checked_batch_sources(batch, collector, load_config(args.workspace, global_only=True), checked)
                verify_checked_selection(checked, selection, exact=True)
            report = await batch.install()
            from amplifier_web.profile_catalog import save_catalog
            save_catalog(load_config(args.workspace, global_only=True), profiles, catalog)
            facts.update(ok=True, stage='prepared', dependenciesPrepared=True, **report)
        elif args.refresh_dependencies:
            from amplifier_web.host.session import prepare_dependencies
            await prepare_dependencies(args.workspace,bundle=args.bundle,install_overrides=args.install_overrides, global_only=args.global_only, runtime_plan=runtime_plan)
            facts.update(ok=True,stage='prepared',dependenciesPrepared=True)
        else:
            from amplifier_web.host.session import prepare_manager
            session,runtime,report=await prepare_manager(args.workspace,bundle=args.bundle,resume=False,ask=deny,
                install_overrides=args.install_overrides, qualification_readonly=args.read_only, global_only=args.global_only, runtime_plan=runtime_plan)
            facts.update(stage='capabilities',standalone=bool(report.get('standalone')),providersPresent=bool(report.get('providers')))
            if not facts['standalone'] or not facts['providersPresent']:raise RuntimeError('Incomplete staged runtime')
            facts['cliAbsent']=not any(importlib.util.find_spec(name) for name in ('amplifier_app_cli','amplifier_loop_live_cli','amplifier_workspace'))
            if not facts['cliAbsent']:raise RuntimeError('A CLI host dependency was introduced')
            facts.update(ok=True,stage='complete')
    except Exception as error:
        facts.update(probe_failure(error,facts['stage']))
        # Keep the encountered reference private and bound to this attempt.
        # No bundle/config composition is repeated on an automatic retry tick.
        import os
        from amplifier_web.update_health import retain_environment_failure
        try:
            retain_environment_failure(error, Path(os.environ['AMPLIFIER_WEB_HOME']),
                                       os.environ.get('AMPLIFIER_UPDATE_ATTEMPT'))
        except (OSError, ValueError, KeyError):
            pass  # Missing recovery evidence retains the original refusal.
    finally:
        if session:
            try:await session.cleanup()
            except Exception as error:facts.update(ok=False,stage='cleanup',errorType=exception_type(error))
    facts['elapsedMs'] = round((time.monotonic() - started)*1000)
    print(PROBE_PREFIX+json.dumps(facts),flush=True)
    if not facts['ok']:raise SystemExit(1)

asyncio.run(main())
