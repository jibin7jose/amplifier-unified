"""Run the real activation admission body without importing an app or providers."""
import ast
import asyncio
import builtins
import copy
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class _RestartRejected(ValueError):
    pass


class _AfterPlatformGate(Exception):
    pass


class NativeActivationAdmissionTests(unittest.IsolatedAsyncioTestCase):
    def owner(self, platform):
        source = Path(__file__).resolve().parents[1] / "amplifier_web/app_updates.py"
        node = next(n for n in ast.parse(source.read_text()).body
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == "_activate")
        readiness = source.with_name("update_readiness.py")
        intent = next(n for n in ast.parse(readiness.read_text()).body
                      if isinstance(n, ast.FunctionDef) and n.name == "valid_update_intent")
        intent_namespace = {}
        exec(compile(ast.Module(body=[intent], type_ignores=[]), str(readiness), "exec"),
             intent_namespace)
        def imports(name, *args, **kwargs):
            if name == "update_readiness":
                return SimpleNamespace(valid_update_intent=intent_namespace["valid_update_intent"])
            if name == "update_restart":
                return SimpleNamespace(RestartRejected=_RestartRejected)
            raise AssertionError("Activation escaped its platform admission: " + name)
        namespace = {
            "__builtins__": {**vars(builtins), "__import__": imports},
            "sys": SimpleNamespace(platform=platform), "re": re, "copy": copy,
        }
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
        return namespace["_activate"]

    def manager(self, **extra):
        state = {"phase": "app-staged", "pendingApp": {
            "revision": "a" * 40, "generation": "b" * 32, "latest": "v0.68.0",
            "extras": []}, **extra}
        class Manager:
            service = SimpleNamespace(state={"updates": state})
            diagnostics = SimpleNamespace(begin=Mock(), record=Mock())
            @property
            def directory(self):
                raise _AfterPlatformGate("Reached candidate files/replacement path")
            async def publish(self, **values):
                state.update(values)
        return Manager(), state

    async def test_windows_refuses_before_candidate_files_or_any_replacement(self):
        manager, state = self.manager()
        pending = copy.deepcopy(state["pendingApp"])
        await self.owner("win32")(manager)
        self.assertEqual(state["phase"], "error")
        self.assertEqual(state["pendingApp"], pending)
        self.assertIn("qualified manual installation handoff", state["error"])
        self.assertIn("restarting the current launcher alone does not install", state["error"])
        self.assertNotIn("pendingReplacement", state)
        self.assertNotIn("pendingRestart", state)
        manager.diagnostics.record.assert_called_once_with(
            "restart-capability", "failed", errorType="RuntimeError")

    async def test_windows_force_is_not_an_escape_and_keeps_the_candidate(self):
        manager, state = self.manager()
        pending = copy.deepcopy(state["pendingApp"])
        with self.assertRaisesRegex(_RestartRejected, "Automatic Windows"):
            await self.owner("win32")(manager, force={"already": "confirmed"})
        self.assertEqual(state["pendingApp"], pending)
        self.assertEqual(state["phase"], "error")

    async def test_existing_unknown_replacement_is_never_cleared(self):
        original = {"attemptId": "unknown-old-attempt", "outcome": "unknown"}
        manager, state = self.manager(pendingReplacement=copy.deepcopy(original))
        before = copy.deepcopy(state)
        await self.owner("win32")(manager)
        self.assertEqual(state, before)
        manager.diagnostics.begin.assert_not_called()
        manager.diagnostics.record.assert_not_called()

    async def test_existing_restart_receipt_is_not_relabelled_as_no_effect(self):
        manager, state = self.manager(
            phase="interrupted", pendingRestart={"attemptId": "unknown", "version": "0.68.0"})
        before = copy.deepcopy(state)
        with self.assertRaisesRegex(_RestartRejected, "earlier application handoff"):
            await self.owner("win32")(manager, force={"already": "confirmed"})
        self.assertEqual(state, before)
        manager.diagnostics.begin.assert_not_called()

    async def test_no_pending_candidate_does_not_invent_a_failure(self):
        manager, state = self.manager(pendingApp=None)
        await self.owner("win32")(manager)
        self.assertEqual(state["phase"], "app-staged")
        manager.diagnostics.begin.assert_not_called()
        manager.diagnostics.record.assert_not_called()

    async def test_invalid_intent_refuses_before_platform_or_replacement_effects(self):
        for platform in ("win32", "linux", "darwin"):
            for scope in ([], {}, "other"):
                with self.subTest(platform=platform, scope=scope):
                    manager, state = self.manager(applicationIntent={"id": "request", "scope": scope})
                    before = copy.deepcopy(state)
                    await self.owner(platform)(manager)
                    self.assertEqual(state, before)
                    manager.diagnostics.begin.assert_not_called()
                    manager.diagnostics.record.assert_not_called()

    async def test_posix_retains_its_existing_activation_path(self):
        for platform in ("linux", "darwin"):
            with self.subTest(platform=platform):
                manager, state = self.manager()
                with self.assertRaises(_AfterPlatformGate):
                    await self.owner(platform)(manager)
                self.assertEqual(state["phase"], "app-staged")
                manager.diagnostics.record.assert_not_called()


if __name__ == "__main__":
    unittest.main()