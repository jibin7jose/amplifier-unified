"""Actual installed hook + Core child/resume lifecycle; no model/network effects."""
import asyncio
from dataclasses import dataclass
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from amplifier_foundation.bundle import Bundle, PreparedBundle, BundleModuleResolver
from amplifier_module_loop_live.runtime import Runtime
from amplifier_web.decision_accounts import DecisionAccounts, AccountResolver, ACCOUNTS, ENVELOPE
from amplifier_web.host.children import install_children
from amplifier_web.host.storage import SessionStore
from amplifier_web.provider_isolation import ProviderIsolation

network = []
def offline(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
        network.append(event)
        raise RuntimeError("Decision child probe forbids networking")
sys.addaudithook(offline)

created = []
executed = []
partial = []
class FiniteOrchestrator:
    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        assert not providers, "Decision hook must not create a chat provider"
        backend = coordinator.get_capability("decision.backends")["child-decision"]
        assert not backend.closed
        executed.append(coordinator.session_id)
        await context.add_message({"role": "user", "content": prompt})
        await context.add_message({"role": "assistant", "content": "Completed fixture"})
        return "Completed fixture"

@dataclass
class ProbePrepared(PreparedBundle):
    fail_after_ready: bool = False
    async def create_session(self, **kwargs):
        if self.fail_after_ready:
            original = kwargs["before_initialize"]
            async def initialize(session):
                await original(session)
                report = session.coordinator.get_capability("decision.account_lifecycle")
                ready = 0
                async def lifecycle(binding, module, outcome, error=None):
                    nonlocal ready
                    if outcome == "ready":
                        ready += 1
                        if ready == 2:
                            partial.append((session, session.coordinator.get_capability("decision.backends")["child-decision"]))
                            raise asyncio.CancelledError("fixture cancels a later account")
                    return await report(binding, module, outcome, error)
                session.coordinator.register_capability("decision.account_lifecycle", lifecycle)
            kwargs["before_initialize"] = initialize
        session = await super().create_session(**kwargs)
        table = session.coordinator.get_capability("decision.backends") or {}
        created.append((session, table.get("child-decision")))
        await session.coordinator.mount("orchestrator", FiniteOrchestrator())
        return session

async def main():
    results = []
    for family, model in (("jev", "jev-1.14.0"), ("local-decisions", "lightning-int8")):
        module = "provider-" + family
        package = "amplifier_module_" + module.replace("-", "_")
        package_root = Path(importlib.util.find_spec(package).origin).parent.parent
        source = "installed-fixture://" + module
        paths = {name: Path(importlib.util.find_spec("amplifier_module_" + name.replace("-", "_")).origin).parent.parent
                 for name in ("loop-live", "context-simple")}
        resolver = BundleModuleResolver(paths, source_paths={(module, source): package_root})
        logical = {"session": {"orchestrator": {"module": "loop-live"}, "context": {"module": "context-simple"}},
            "providers": [{"module": module, "instance_id": "private-decision", "source": source,
                "config": {"api_key": "synthetic-unused-private-key", "priority": -1000,
                    "decisions": {"enabled": True, "backends": {"child-decision": {"enabled": True, "model": model}}}}}],
            "agents": {"worker": {"instruction": "Run offline fixture"}}}
        isolation = ProviderIsolation()
        account = DecisionAccounts(logical, isolation, lambda module, source: source)
        # A lost exact source must not silently fall back to this installed hook.
        from amplifier_core.module_sources import ModuleNotFoundError as SourceNotFoundError
        from amplifier_core import AmplifierSession
        from amplifier_web.module_failures import ConfiguredModuleError
        class MissingSource:
            async def async_resolve(self, requested, source_hint=None):
                if requested == module:
                    raise SourceNotFoundError("fixture-selected-source-disappeared")
                return await resolver.async_resolve(requested, source_hint=source_hint)
        rejected = AmplifierSession(account.plan)
        await rejected.coordinator.mount("module-source-resolver", AccountResolver(MissingSource(), account.aliases))
        await account.before_initialize(rejected)
        try:
            await rejected.initialize()
            with_missing = False
            try:
                account.require_complete()
            except ConfiguredModuleError:
                with_missing = True
            assert with_missing and not rejected.coordinator.get_capability("decision.backends")
        finally:
            await rejected.cleanup()
        prepared = ProbePrepared(account.plan, AccountResolver(resolver, account.aliases), Bundle.from_dict(logical))
        async def initialize(session):
            await isolation.before_initialize(session)
            await account.before_initialize(session)
        parent = await prepared.create_session(session_id="decision-parent-" + family, before_initialize=initialize)
        account.require_complete()
        owned_parent = created[-1][1]
        try:
            with tempfile.TemporaryDirectory() as temporary:
                store = SessionStore(Path(temporary) / "sessions")
                registry = await install_children(parent, prepared, Runtime(parent.session_id), store, None)
                child_id = "decision-child-" + family
                first = await registry.spawn("worker", "first", parent, sub_session_id=child_id)
                assert first["output"] == "Completed fixture"
                first_child, first_backend = created[-1]
                assert first_backend.closed and not owned_parent.closed
                _, metadata = store.load(child_id)
                serialized = json.dumps(metadata)
                assert "synthetic-unused-private-key" not in serialized and ACCOUNTS not in serialized and ENVELOPE not in serialized
                assert metadata["mount_plan"]["providers"][0]["source"] == source
                assert metadata["mount_plan"]["providers"][0]["config"]["api_key"] == {"$hostProviderCredential": True}
                resumed = await registry.resume(child_id, "resume", parent)
                assert resumed["output"] == "Completed fixture"
                second_child, second_backend = created[-1]
                assert second_backend is not first_backend and second_backend.closed and not owned_parent.closed
                first_binding = first_child.coordinator.config["session"][ACCOUNTS][0]["binding_id"]
                second_binding = second_child.coordinator.config["session"][ACCOUNTS][0]["binding_id"]
                assert first_binding != second_binding
                prior = len(executed)
                rows, saved = store.load(child_id)
                saved["mount_plan"]["providers"][0]["source"] += "-forged"
                store.save(child_id, rows, saved)
                try:
                    await registry.resume(child_id, "must not execute", parent)
                except ValueError as exc:
                    assert "no longer authorized" in str(exc), str(exc)
                else:
                    raise AssertionError("Forged child account source was accepted")
                assert len(executed) == prior and not owned_parent.closed
                # Actual second-account cancellation occurs before create_session
                # returns; the child host must still dispose the first account.
                prepared.fail_after_ready = True
                second_row = json.loads(json.dumps(logical["providers"][0]))
                second_row["instance_id"] = "second-decision-account"
                second_row["config"]["decisions"]["backends"] = {"second-decision": {"enabled": True, "model": model}}
                try:
                    await registry.spawn("two", "must not execute", parent,
                        agent_configs={"two": {"providers": [second_row]}}, sub_session_id="failed-child-" + family)
                except (RuntimeError, asyncio.CancelledError):
                    pass
                else:
                    raise AssertionError("Later account cancellation was admitted")
                finally:
                    prepared.fail_after_ready = False
                assert len(executed) == prior
                partial_session, partial_backend = partial[-1]
                assert partial_backend.closed and not partial_session.coordinator.get_capability("decision.backends")
                assert not owned_parent.closed
                results.append({"family": family, "child_resume": True, "rejected_source_switch": True,
                    "closed_children": True, "parent_survived": True, "fresh_binding": True,
                    "selected_source_loss_refused": True, "failed_initialization_cleaned_first_account": True,
                    "installed_source_mapping": str(package_root)})
        finally:
            await parent.cleanup()
        assert owned_parent.closed
    assert not network
    print(json.dumps({"passed": True, "child_resume": True, "rejected_source_switch": True,
        "core": importlib.metadata.version("amplifier-core"), "cases": results, "networkAttempts": network,
        "limits": "Installed package mapping and synthetic child orchestrator; no inference or device actions"}))

if __name__ == "__main__":
    asyncio.run(main())
