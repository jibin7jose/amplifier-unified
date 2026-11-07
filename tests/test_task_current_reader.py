"""Bounded current-task reads omit historical task validation, not JSON parsing."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_web.task_continuity import _load_control


def saved(tmp_path, **fields):
    service = SimpleNamespace(data_dir=tmp_path)
    session = {"id": "public-owner", "nativeIdentity": "native-owner"}
    path = tmp_path / "sessions" / "native-owner" / "control-state.json"
    path.parent.mkdir(parents=True)
    value = {"task": None, **fields}
    path.write_text(json.dumps(value))
    return service, session, path, value


def task():
    return {"id": "current-task", "revision": 2, "status": "paused",
            "objective": "Finish the accepted work", "constraints": [],
            "questionIds": [], "operationIds": ["retained-operation"],
            "artifactRefs": [], "completionEvidence": [], "corrections": [],
            "changes": [{"revision": 2, "fields": {"status": "paused"}}],
            "provenance": {"source": "trusted-system"}}


def test_current_task_read_preserves_canonical_fields_without_history_authority(tmp_path):
    service, session, path, value = saved(tmp_path, task=task(),
        taskHistory="Not current authority", taskReceipts=[], goal="Not inspected")
    before = path.read_bytes()
    assert _load_control(service, session, current_only=True, max_bytes=256 * 1024) == {
        "task": value["task"]}
    with pytest.raises(ValueError, match="unavailable"):
        _load_control(service, session)
    assert path.read_bytes() == before


def test_normal_task_read_still_returns_history_receipts_and_goal(tmp_path):
    service, session, path, value = saved(tmp_path, task=task(), taskHistory=[task()],
        taskReceipts={"original": {"receipt": "kept"}}, goal={"status": "paused"})
    before = path.read_bytes()
    assert _load_control(service, session) == value
    assert path.read_bytes() == before


def test_current_read_refuses_oversized_authority_before_opening_body(tmp_path, monkeypatch):
    service, session, path, _ = saved(tmp_path, unrelated="x" * (256 * 1024))
    original = Path.open

    def observed(target, *args, **kwargs):
        if target == path:
            pytest.fail("Oversized task authority must be rejected before body allocation")
        return original(target, *args, **kwargs)

    monkeypatch.setattr(Path, "open", observed)
    with pytest.raises(ValueError, match="unavailable"):
        _load_control(service, session, current_only=True, max_bytes=256 * 1024)


def test_current_read_refuses_replaced_authority_after_read(tmp_path, monkeypatch):
    service, session, path, _ = saved(tmp_path)
    original = Path.open

    class ReplacedDuringRead:
        def __enter__(self):
            self.stream = original(path, "rb")
            return self

        def read(self, size):
            raw = self.stream.read(size)
            replacement = path.with_suffix(".replacement")
            replacement.write_text(json.dumps({"task": task()}))
            replacement.replace(path)
            return raw

        def __exit__(self, *args):
            self.stream.close()

    def observed(target, *args, **kwargs):
        return ReplacedDuringRead() if target == path else original(target, *args, **kwargs)

    monkeypatch.setattr(Path, "open", observed)
    with pytest.raises(ValueError, match="unavailable"):
        _load_control(service, session, current_only=True, max_bytes=256 * 1024)
    with original(path) as stream:
        assert json.load(stream)["task"] == task()


@pytest.mark.parametrize("limit", [0, -2, True])
def test_invalid_limit_never_turns_bounded_read_into_unlimited_read(tmp_path, limit):
    service, session, _, _ = saved(tmp_path)
    with pytest.raises(ValueError, match="positive"):
        _load_control(service, session, current_only=True, max_bytes=limit)
