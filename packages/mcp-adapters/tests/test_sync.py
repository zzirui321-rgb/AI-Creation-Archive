import copy
import hashlib
import io
import time
import zipfile

import httpx
import pytest
from aica_mcp.coordinator import Coordinator
from aica_mcp.transfer import build_bundle
from aica_schema.protocol import dumps, loads, new_id, utc_now


class RemoteArchive:
    def __init__(self, directory):
        self.directory = directory
        self.head = None
        self.documents = {}
        self.objects = {}
        self.imports = {}
        self.receipts = {}
        self.calls = []
        self.race = False
        self.lose_commit = False
        self.storage_rewrite = False

    def save(self, document):
        self.head = new_id()
        self.documents[self.head] = copy.deepcopy(document)

    def edit(self, title=None, file_content=None):
        document = copy.deepcopy(self.documents[self.head])
        if title:
            document["metadata"]["title"] = title
        if file_content is not None:
            old = document["extensions"]["aica.sync.files"]["output.txt"]
            artifact = copy.deepcopy(next(item for item in document["artifacts"] if item["id"] == old))
            sha = hashlib.sha256(file_content).hexdigest()
            artifact.update(id="remote-" + new_id(), sha256=sha, size=len(file_content), uri=".aica/artifacts/" + sha)
            self.objects[sha] = file_content
            document["artifacts"].append(artifact)
            document["extensions"]["aica.sync.files"]["output.txt"] = artifact["id"]
        self.save(document)

    def handle(self, request):
        self.calls.append((request.method, request.url.path))
        if request.url.path.endswith("/branches"):
            return httpx.Response(200, json={"status": "success", "branches": [{"name": "main", "head": self.head}]})
        if request.url.path.endswith("/imports"):
            raw = request.read()
            key = hashlib.sha256(raw).hexdigest()
            if key not in self.receipts:
                with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
                    manifest = loads(bundle.read("manifest.json").decode())
                    document = loads(bundle.read("aica.json").decode())
                    for item in manifest["artifacts"]:
                        self.objects[item["sha256"]] = bundle.read(item["path"])
                identifier = new_id()
                self.imports[identifier] = document
                self.receipts[key] = {"status": "success", "format": "aica-transfer/0.1", "project_id": PROJECT,
                                      "import_id": identifier, "bundle_sha256": key, "archive_sha256": manifest["archive"]["sha256"]}
            return httpx.Response(201, json=self.receipts[key])
        if request.url.path.endswith("/archive-commits"):
            body = loads(request.read().decode())
            if self.race:
                self.race = False
                self.edit(title="Concurrent platform edit")
            if body["expected_head"] != self.head:
                return httpx.Response(409, json={"status": "error", "code": "id_conflict"})
            self.save(self.imports[body["import_id"]])
            if self.lose_commit:
                self.lose_commit = False
                raise httpx.ReadError("Fixture lost commit response", request=request)
            return httpx.Response(201, json={"status": "success", "commit": {"id": self.head}})
        assert request.url.path.endswith("/archive"), request.url.path
        document = copy.deepcopy(self.documents[request.url.params["commit_id"]])
        for artifact in document["artifacts"]:
            if self.storage_rewrite:
                artifact["uri"] = ".aica/imports/" + artifact["sha256"]
                artifact.pop("locations", None)
            path = self.directory / artifact["uri"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.objects[artifact["sha256"]])
        bundle = self.directory / "transfer.zip"
        bundle.unlink(missing_ok=True)
        build_bundle(self.directory, bundle, archive_bytes=dumps(document).encode())
        return httpx.Response(200, content=bundle.read_bytes())


PROJECT = "00000000-0000-4000-8000-000000000001"
REPO = "00000000-0000-4000-8000-000000000002"


@pytest.fixture
def sync_case(tmp_path, monkeypatch):
    monkeypatch.setenv("AICA_CREDENTIALS_DIR", str(tmp_path / "credentials"))
    project = tmp_path / "local"
    project.mkdir()
    remote_dir = tmp_path / "remote"
    remote_dir.mkdir()
    coordinator = Coordinator(project)
    remote = RemoteArchive(remote_dir)
    config = {"version": 1, "server_url": "https://platform.invalid", "project_id": PROJECT,
              "token_env": "AICA_UPLOAD_TOKEN", "auto_upload": True, "interval_seconds": 1,
              "allow_loopback_http": False, "sync": {"repo_id": REPO, "branch": "main"}}
    credential = {"version": 1, "identity": coordinator.platform.credentials.identity(config), "grant_id": new_id(),
                  "enabled": True, "upload_token": "U" * 48, "upload_token_id": new_id(), "upload_expires_at": time.time() + 3600,
                  "login_token": "L" * 48, "login_expires_at": time.time() + 3600, "sync_repo_id": REPO, "sync_files": True}
    coordinator.platform.connect(config, credential)
    coordinator.platform.transport = httpx.MockTransport(remote.handle)
    yield coordinator, remote, config
    coordinator.platform.close()
    coordinator.recorder.close()


def register(coordinator, content=b"original"):
    recorder = coordinator.recorder
    path = recorder.project / "output.txt"
    path.write_bytes(content)
    previous = recorder.recording_permission
    recorder.recording_permission = lambda: None
    try:
        session = recorder.start_session(new_id(), True)
        source = new_id()
        event = {"event_id": new_id(), "source_id": source, "source_seq": 1, "session_id": session["session_id"],
                 "agent_id": None, "type": "artifact.recorded", "timestamp": utc_now(), "causal_parents": [], "payload": {}}
        recorder.capture_artifact(session["session_token"], event, "output.txt", "text/plain")
        event.update(event_id=new_id(), source_seq=2, type="session.closed", payload={})
        recorder.append(event, session["session_token"])
    finally:
        recorder.recording_permission = previous


def tick(coordinator):
    coordinator.sync.next_poll = 0
    coordinator.platform.run_once()
    config = coordinator.platform.read_config()
    state = coordinator.sync.read_state(config)
    return state


def test_local_edit_platform_edit_source_file_and_no_echo(sync_case):
    coordinator, remote, config = sync_case
    register(coordinator)
    state = tick(coordinator)
    assert state["status"] == "synced" and state["head"] == remote.head
    path = coordinator.recorder.project / "output.txt"
    path.write_bytes(b"local edit")
    assert tick(coordinator)["status"] == "synced"
    assert b"local edit" in remote.objects.values()
    remote.edit(title="Platform title", file_content=b"platform edit")
    expected_head = remote.head
    state = tick(coordinator)
    assert state["status"] == "synced", state
    assert path.read_bytes() == b"platform edit"
    assert coordinator.recorder.document["metadata"]["title"] == "Platform title"
    assert coordinator.platform.project_access()["state"] == "active"
    before = len([call for call in remote.calls if call[0] == "POST"])
    tick(coordinator)
    assert remote.head == expected_head
    assert len([call for call in remote.calls if call[0] == "POST"]) == before
    assert list((coordinator.recorder.directory / "backups").glob("sync-*/recording.sqlite3"))


@pytest.mark.parametrize("choice", ["local", "remote"])
def test_both_sides_pause_then_explicit_pinned_choice(sync_case, choice):
    coordinator, remote, config = sync_case
    register(coordinator)
    tick(coordinator)
    path = coordinator.recorder.project / "output.txt"
    path.write_bytes(b"local version")
    remote.edit(file_content=b"remote version")
    head = remote.head
    state = tick(coordinator)
    assert state["status"] == "conflict" and path.read_bytes() == b"local version" and remote.head == head
    coordinator.sync.control("resolve", choice=choice, conflict_id=state["conflict"]["id"])
    state = tick(coordinator)
    assert state["status"] == "synced", state
    assert path.read_bytes() == (b"local version" if choice == "local" else b"remote version")


def test_compare_and_swap_race_never_replaces_platform_edit(sync_case):
    coordinator, remote, config = sync_case
    register(coordinator)
    tick(coordinator)
    (coordinator.recorder.project / "output.txt").write_bytes(b"local edit")
    remote.race = True
    state = tick(coordinator)
    assert state["status"] == "conflict", state
    assert remote.documents[remote.head]["metadata"]["title"] == "Concurrent platform edit"
    assert state["pending"] and (coordinator.sync.directory(config) / "sync-pending.zip").exists()


def test_lost_commit_reply_recovers_without_duplicate_commit(sync_case):
    coordinator, remote, config = sync_case
    register(coordinator)
    remote.lose_commit = True
    state = tick(coordinator)
    assert state["pending"] and remote.head
    head = remote.head
    coordinator.sync.control("resume")
    state = tick(coordinator)
    assert state["status"] == "synced" and remote.head == head
    assert state["pending"] is None and len(remote.documents) == 1


def test_pause_busy_turn_and_expired_login_send_nothing(sync_case):
    coordinator, remote, config = sync_case
    register(coordinator)
    coordinator.sync.control("pause")
    tick(coordinator)
    assert not remote.calls
    coordinator.sync.control("resume")
    coordinator.turns.turns[("codex", "h", "t")] = {"state": "recording"}
    assert tick(coordinator)["status"] == "waiting_for_turn"
    assert not remote.calls
    coordinator.turns.turns.clear()
    credential = coordinator.platform.credentials.read(config)
    credential["login_expires_at"] = time.time() - 1
    coordinator.platform.credentials.write(config, credential)
    state = tick(coordinator)
    assert state["error"]["code"] == "credential_expired" and not remote.calls


def test_symlink_and_unregistered_collision_do_not_overwrite(sync_case, tmp_path):
    coordinator, remote, config = sync_case
    register(coordinator)
    tick(coordinator)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"private")
    path = coordinator.recorder.project / "output.txt"
    path.unlink()
    path.symlink_to(outside)
    remote.edit(file_content=b"remote")
    state = tick(coordinator)
    assert state["status"] == "error" and outside.read_bytes() == b"private"
    assert state["error"]["code"] == "unsafe_sync_path"


def test_interrupted_file_install_rolls_back_before_retry(sync_case, monkeypatch):
    coordinator, remote, config = sync_case
    register(coordinator)
    tick(coordinator)
    remote.edit(title="new platform title", file_content=b"remote bytes")
    original = coordinator.sync.install_database

    def fail(document, identifier):
        raise OSError("Fixture interruption before database commit")

    monkeypatch.setattr(coordinator.sync, "install_database", fail)
    state = tick(coordinator)
    assert state["incoming"]
    assert (coordinator.recorder.project / "output.txt").read_bytes() == b"remote bytes"
    coordinator.sync.recover(config, state)
    assert (coordinator.recorder.project / "output.txt").read_bytes() == b"original"
    assert coordinator.recorder.document["metadata"]["title"] != "new platform title"
    monkeypatch.setattr(coordinator.sync, "install_database", original)
    coordinator.sync.control("resume")
    assert tick(coordinator)["status"] == "synced"


def test_local_delete_and_remote_delete_keep_recoverable_backups(sync_case):
    coordinator, remote, config = sync_case
    register(coordinator)
    tick(coordinator)
    path = coordinator.recorder.project / "output.txt"
    path.unlink()
    state = tick(coordinator)
    assert state["status"] == "synced", state
    assert remote.documents[remote.head]["extensions"]["aica.sync.files"]["output.txt"] is None


def test_restart_reuses_baseline_and_authorization(sync_case):
    coordinator, remote, config = sync_case
    register(coordinator)
    tick(coordinator)
    root = coordinator.recorder.project
    coordinator.recorder.close()
    recovered = Coordinator(root)
    coordinator.recorder = recovered.recorder
    try:
        recovered.platform.transport = httpx.MockTransport(remote.handle)
        before = len(remote.calls)
        state = tick(recovered)
        assert state["status"] == "synced"
        assert not any(method == "POST" for method, path in remote.calls[before:])
        assert recovered.platform.project_access()["state"] == "active"
    finally:
        recovered.platform.close()


def test_remote_delete_updates_file_and_retains_backup(sync_case):
    coordinator, remote, config = sync_case
    register(coordinator)
    tick(coordinator)
    document = copy.deepcopy(remote.documents[remote.head])
    document['extensions']['aica.sync.files']['output.txt'] = None
    remote.save(document)
    assert tick(coordinator)['status'] == 'synced'
    assert not (coordinator.recorder.project / 'output.txt').exists()
    backups = list((coordinator.recorder.directory / 'backups').glob('sync-*/file-*'))
    assert any(path.read_bytes() == b'original' for path in backups)


def test_pause_during_upload_survives_completion(sync_case, monkeypatch):
    coordinator, remote, config = sync_case
    register(coordinator)
    request = coordinator.sync.request

    def pausing(config, method, path, body=None):
        if path.endswith('/archive-commits'):
            coordinator.sync.control('pause')
        return request(config, method, path, body)

    monkeypatch.setattr(coordinator.sync, 'request', pausing)
    tick(coordinator)
    state = coordinator.sync.read_state(config)
    assert state['paused'] is True
    calls = len(remote.calls)
    tick(coordinator)
    assert len(remote.calls) == calls


def test_cas_conflict_remote_choice_preserves_pending_and_local_file(sync_case):
    coordinator, remote, config = sync_case
    register(coordinator)
    tick(coordinator)
    (coordinator.recorder.project / 'output.txt').write_bytes(b'local change')
    remote.race = True
    state = tick(coordinator)
    assert state['conflict'] and state['pending']
    coordinator.sync.control('resolve', 'remote', state['conflict']['id'])
    assert tick(coordinator)['status'] == 'synced'
    assert coordinator.sync.read_state(config)['pending'] is None
    assert list((coordinator.recorder.directory / 'backups').glob('sync-outgoing-*/pending.zip'))
    calls = len(remote.calls)
    assert tick(coordinator)['status'] == 'synced'
    assert all(path.endswith('/branches') for _, path in remote.calls[calls:])


def test_lost_commit_response_survives_platform_storage_rewrite(sync_case):
    coordinator, remote, config = sync_case
    register(coordinator)
    remote.storage_rewrite = True
    remote.lose_commit = True
    state = tick(coordinator)
    assert state['pending'] and state['status'] == 'error'
    coordinator.sync.control('resume')
    assert tick(coordinator)['status'] == 'synced'
    assert coordinator.sync.read_state(config)['pending'] is None


def test_remote_unregistration_preserves_working_file(sync_case):
    coordinator, remote, config = sync_case
    register(coordinator)
    tick(coordinator)
    document = copy.deepcopy(remote.documents[remote.head])
    document['extensions']['aica.sync.files'].pop('output.txt')
    remote.save(document)
    assert tick(coordinator)['status'] == 'synced'
    assert (coordinator.recorder.project / 'output.txt').read_bytes() == b'original'


def test_acknowledged_bundle_left_by_interruption_is_reconciled(sync_case, monkeypatch):
    coordinator, remote, config = sync_case
    register(coordinator)
    finish = coordinator.sync.finish_upload

    def interrupted_cleanup(config, state, head):
        path = coordinator.sync.directory(config) / 'sync-pending.zip'
        content = path.read_bytes()
        finish(config, state, head)
        path.write_bytes(content)

    monkeypatch.setattr(coordinator.sync, 'finish_upload', interrupted_cleanup)
    assert tick(coordinator)['status'] == 'synced'
    monkeypatch.setattr(coordinator.sync, 'finish_upload', finish)
    (coordinator.recorder.project / 'output.txt').write_bytes(b'next local change')
    assert tick(coordinator)['status'] == 'synced'
    assert not (coordinator.sync.directory(config) / 'sync-pending.zip').exists()


def test_network_failure_while_refreshing_conflict_preserves_pending(sync_case, monkeypatch):
    coordinator, remote, config = sync_case
    register(coordinator)
    tick(coordinator)
    (coordinator.recorder.project / 'output.txt').write_bytes(b'local change')
    remote.race = True
    original = coordinator.sync.remote_head
    calls = []

    def head(config):
        calls.append(True)
        if len(calls) == 2:
            raise httpx.ReadTimeout('Fixture conflict refresh interrupted')
        result = original(config)
        return result

    monkeypatch.setattr(coordinator.sync, 'remote_head', head)
    state = tick(coordinator)
    assert state['status'] == 'error' and state['pending']
    assert state['failures'] == 1
    assert remote.documents[remote.head]['metadata']['title'] == 'Concurrent platform edit'
