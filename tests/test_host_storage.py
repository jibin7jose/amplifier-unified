import json
import os
from pathlib import Path
import pytest

from amplifier_web.host.storage import SessionStore
from amplifier_web.host.approvals import Approvals


@pytest.mark.parametrize('persistent', [False, True])
def test_metadata_write_during_read_is_retried_once(tmp_path, monkeypatch, persistent):
    from amplifier_foundation.session import history
    store = SessionStore(tmp_path / 'sessions')
    store.save('root', [{'role': 'user', 'content': 'Keep my question'}], {'status': 'idle'})
    transcript = store.directory('root') / 'transcript.jsonl'
    before = transcript.read_bytes()
    original = history._read_metadata
    calls = 0

    def racing_read(path, **kwargs):
        nonlocal calls
        result = original(path, **kwargs)
        calls += 1
        if persistent or calls == 1:
            path.write_text(json.dumps({**result, 'status': f'updated-{calls}'}))
        return result

    monkeypatch.setattr(history, '_read_metadata', racing_read)
    if persistent:
        with pytest.raises(ValueError, match='changed while'):
            store.load('root')
    else:
        rows, metadata = store.load('root')
        assert rows == [{'role': 'user', 'content': 'Keep my question'}]
        assert metadata['status'] == 'updated-1'
    # Successful loads separately refresh naming metadata after the snapshot.
    assert calls == (2 if persistent else 3)
    assert transcript.read_bytes() == before


def test_changing_transcript_is_not_retried(tmp_path, monkeypatch):
    from amplifier_foundation.session import history
    store = SessionStore(tmp_path / 'sessions')
    store.save('root', [{'role': 'user', 'content': 'Original'}], {})
    original = history._read_messages
    calls = 0

    def racing_read(path, **kwargs):
        nonlocal calls
        rows = original(path, **kwargs)
        calls += 1
        path.write_text(json.dumps({'role': 'user', 'content': 'Newer work'}) + '\n')
        return rows

    monkeypatch.setattr(history, '_read_messages', racing_read)
    with pytest.raises(ValueError, match='changed while'):
        store.load('root')
    assert calls == 1
    assert 'Newer work' in (store.directory('root') / 'transcript.jsonl').read_text()


def test_checkpoint_roundtrip_keeps_transcript_and_removes_credentials(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    messages = [{"role": "system", "content": "regenerate"}, {"role": "user", "content": "continue"}]
    store.save("child-123", messages, {"parent_id": "root", "config": {"api_key": "do-not-save", "model": "chosen-model"}})
    rows, metadata = store.load("child-123")
    assert rows == messages[1:]
    assert metadata["parent_id"] == "root"
    assert metadata["config"] == {"model": "chosen-model"}
    assert (tmp_path / "sessions/child-123/transcript.jsonl").stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / "sessions/child-123/checkpoint.json").exists()
    assert not any("do-not-save" in p.read_text() for p in (tmp_path / "sessions/child-123").iterdir())


def test_checkpoint_preserves_credential_binding_marker_but_never_a_literal_or_embedded_secret(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    marker = {"$hostProviderCredential": True}
    store.save("bound-child", [], {"mount_plan": {"providers": [
        {"module": "provider-test", "source": "fixture://exact", "config": {
            "api_key": marker, "access_token": "private-token", "secret": {**marker, "value": "private-value"}}}]}})
    config = store.load("bound-child")[1]["mount_plan"]["providers"][0]["config"]
    assert config == {"api_key": marker}
    serialized = (store.directory("bound-child") / "metadata.json").read_text()
    assert "private-token" not in serialized and "private-value" not in serialized


@pytest.mark.parametrize("preserve", [False, True])
def test_save_returns_exact_canonical_rows_for_checkpoint_anchors(tmp_path, preserve):
    from amplifier_web.collaboration_input import checkpoint_anchors
    from types import SimpleNamespace
    store = SessionStore(tmp_path / "sessions")
    rows = [{"role": "system", "content": "Regenerate"},
            {"role": "developer", "content": "Ephemeral guidance"},
            {"role": "user", "content": "Current input",
             "metadata": {"amplifier_input": {"version": 1, "id": "input", "kind": "user"}}},
            {"role": "assistant", "content": [{"type": "text", "text": "First"}, {"type": "text", "text": "second"}]}]
    canonical = store.save("root", rows, {}, preserve_system=preserve)
    assert canonical == store.load("root")[0]
    assert store.save("root", rows, {}, preserve_system=preserve) == canonical
    anchors = checkpoint_anchors(canonical, SimpleNamespace(session_id="root", generation={"id": "g", "input_ids": ["input"]}))
    assert anchors[0]["nativeIndex"] == (3 if preserve else 1)


def test_import_is_read_only_once_and_keeps_job_evidence_without_lock(tmp_path):
    legacy = tmp_path / "legacy"
    source = legacy / "projects/example/sessions/previous"
    (source / "live-jobs").mkdir(parents=True)
    (source / "transcript.jsonl").write_text('{"role":"user","content":"saved"}\n')
    (source / "metadata.json").write_text('{"parent_id":"root"}')
    (source / "live-jobs/job-123.json").write_text('{"version":1,"call_id":"call-1","status":"pending"}')
    (source / "live-jobs/owner.lock").write_text('old owner')
    before = {str(p.relative_to(source)): p.read_bytes() for p in source.rglob('*') if p.is_file()}
    store = SessionStore(tmp_path / "own", legacy_home=legacy)
    messages, metadata = store.import_cli("previous")
    assert messages[0]["content"] == "saved"
    assert metadata["legacy_import"]["jobs_replayed"] is False
    assert (store.base_dir / "previous/live-jobs/job-123.json").exists()
    assert not (store.base_dir / "previous/live-jobs/owner.lock").exists()
    store.save("previous", [{"role":"user","content":"new work"}], metadata)
    assert store.import_cli("previous")[0][0]["content"] == "new work"
    assert before == {str(p.relative_to(source)): p.read_bytes() for p in source.rglob('*') if p.is_file()}


def test_session_paths_cannot_escape_store(tmp_path):
    store = SessionStore(tmp_path)
    for identity in ("../escape", "/absolute", "a/b", "a\\b", "..", ""):
        with pytest.raises(ValueError):
            store.save(identity, [], {})


@pytest.mark.asyncio
async def test_approval_callback_is_required_and_timeout_never_allows():
    import asyncio
    with pytest.raises(RuntimeError):
        await Approvals(None, None).request_approval("deploy?", ["allow", "deny"], 1, "allow")
    async def absent(prompt, options):
        await asyncio.sleep(1)
    assert await Approvals(None, absent).request_approval("deploy?", ["allow", "deny"], .001, "allow") == "deny"


@pytest.mark.asyncio
async def test_approval_preserves_user_decision_and_rejects_invented_choice():
    async def allow(prompt, options):
        assert prompt == "apply change?"
        assert options == ["allow", "deny"]
        return "allow"
    assert await Approvals(None, allow).request_approval("apply change?", ["allow", "deny"]) == "allow"
    async def bad(prompt, options):
        return "approve-all-forever"
    with pytest.raises(ValueError):
        await Approvals(None, bad).request_approval("apply?", ["allow", "deny"])


def test_find_prefers_native_backup_over_private_legacy_and_deduplicates_paths(tmp_path):
    from amplifier_web.session_files import sessions_dir
    root = sessions_dir(tmp_path) / 'fixture'
    root.mkdir(parents=True)
    backup = root / 'transcript.jsonl.backup'
    backup.write_text('{"role":"user","content":"native backup"}\n')
    legacy = SessionStore(tmp_path / 'app/sessions')
    legacy.directory('fixture').mkdir()
    (legacy.directory('fixture') / 'checkpoint.json').write_text(json.dumps({'version': 1,
        'messages': [{'role': 'user', 'content': 'stale private'}], 'metadata': {}}))
    rows, _ = SessionStore.find(tmp_path / 'app', 'fixture', tmp_path)
    assert rows[0]['content'] == 'native backup'
    assert not (root / 'transcript.jsonl').exists()
    (root / 'transcript.jsonl').write_bytes(backup.read_bytes())
    assert SessionStore.find(tmp_path / 'app', 'fixture', tmp_path)[0] == rows


def test_explicit_legacy_import_accepts_backup_only_source(tmp_path):
    source = tmp_path / 'legacy/sessions/fixture'
    source.mkdir(parents=True)
    backup = source / 'transcript.jsonl.backup'
    backup.write_text('{"role":"user","content":"saved backup"}\n')
    store = SessionStore(tmp_path / 'own', legacy_home=tmp_path / 'legacy')
    rows, metadata = store.import_cli('fixture')
    assert rows[0]['content'] == 'saved backup'
    assert metadata['legacy_import']['jobs_replayed'] is False
    assert not (source / 'transcript.jsonl').exists()


@pytest.mark.parametrize('preserve_system',[False,True])
def test_noop_checkpoint_updates_metadata_without_changing_transcript_activity(tmp_path,preserve_system):
    store=SessionStore(tmp_path/'sessions')
    rows=[{'role':'system','content':'instructions'},
          {'role':'user','content':'Question'},
          {'role':'assistant','content':'Answer','provider_continuation':{'id':'opaque'}},
          {'role':'user','content':'Internal reminder','metadata':{'ephemeral':True}}]
    store.save('root',rows,{},preserve_system=preserve_system)
    path=store.directory('root')/'transcript.jsonl'
    os.utime(path,(100,100))
    original=(path.read_bytes(),path.stat().st_mtime_ns,path.stat().st_ino)
    store.save('root',rows,{'status':'ready','custom':'preserved'},preserve_system=preserve_system)
    assert (path.read_bytes(),path.stat().st_mtime_ns,path.stat().st_ino)==original
    assert not path.with_name('transcript.jsonl.backup').exists()
    messages,metadata=store.load('root')
    assert messages==(rows if preserve_system else rows[1:])
    assert metadata['status']=='ready' and metadata['custom']=='preserved'
    rows.append({'role':'assistant','content':'Actual new work'})
    store.save('root',rows,{'status':'completed'},preserve_system=preserve_system)
    assert path.stat().st_mtime_ns>original[1]
    assert path.with_name('transcript.jsonl.backup').read_bytes()==original[0]
    assert store.load('root')[0][-1]['content']=='Actual new work'


def test_noop_optimization_still_repairs_corrupt_transcript_from_valid_backup(tmp_path):
    store=SessionStore(tmp_path/'sessions')
    rows=[{'role':'user','content':'Saved'}]
    store.save('root',rows,{})
    path=store.directory('root')/'transcript.jsonl'
    backup=path.with_name('transcript.jsonl.backup')
    backup.write_bytes(path.read_bytes())
    path.write_text('corrupt current transcript')
    store.save('root',rows,{'status':'ready'})
    assert json.loads(path.read_text())==rows[0]
    assert json.loads(backup.read_text())==rows[0]


def test_noop_optimization_does_not_skip_foundation_validation(tmp_path):
    store=SessionStore(tmp_path/'sessions')
    store.save('root',[],{})
    with pytest.raises(ValueError):
        store.save('root',[{'role':'','content':'invalid'}],{})


def test_checkpoint_distinguishes_boolean_and_numeric_continuation_values(tmp_path):
    store=SessionStore(tmp_path/'sessions')
    rows=[{'role':'assistant','content':'Saved','continuation':{'value':True}}]
    store.save('root',rows,{})
    rows[0]['continuation']['value']=1
    store.save('root',rows,{})
    assert type(store.load('root')[0][0]['continuation']['value']) is int


def test_new_app_can_checkpoint_with_pre_index_worker_foundation(tmp_path, monkeypatch):
    """An app update may precede the independently pinned worker update."""
    from amplifier_foundation.session.history import SessionHistoryStore
    from amplifier_web.host.storage import SessionStore
    store = SessionStore(tmp_path)
    original = SessionHistoryStore.save
    def legacy_save(self, messages, metadata, *, preserve_system=False,
                    sanitizer=None, merge_metadata=False):
        return original(self, messages, metadata, preserve_system=preserve_system,
                        sanitizer=sanitizer, merge_metadata=merge_metadata)
    monkeypatch.delattr(SessionHistoryStore, 'indexed_messages')
    monkeypatch.setattr(SessionHistoryStore, 'save', legacy_save)
    messages = [{'role': 'user', 'content': 'Preserve this across the update'}]
    store.save('legacy-worker', messages, {})
    assert store.load('legacy-worker')[0] == messages
