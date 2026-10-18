import copy
import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from aica_mcp.files import ProjectLock, inspect_artifact
from aica_mcp.store import Recorder
from aica_schema.protocol import ProtocolError, dumps, loads, new_id, utc_now, validate_archive


@pytest.fixture
def recorder(tmp_path):
    instance = Recorder(tmp_path)
    yield instance
    instance.close()


def session(recorder, record=True, children=False):
    result = recorder.start_session(new_id(), record, children)
    return result


def event(credentials, sequence, kind, payload, source="test-source", agent_id=None):
    return {"event_id": new_id(), "source_id": source, "source_seq": sequence,
            "session_id": credentials["session_id"], "agent_id": agent_id, "type": kind,
            "timestamp": utc_now(), "causal_parents": [], "payload": payload}


def setup_actor(recorder):
    credentials = session(recorder)
    harness = {"id": new_id(), "name": "Test harness", "version": "1", "config": {}, "state": {}, "availability": {}}
    recorder.append(event(credentials, 1, "harness.registered", harness), credentials["session_token"])
    agent = {"id": new_id(), "session_id": credentials["session_id"], "parent_agent_id": None,
             "harness_id": harness["id"], "client": "test", "model": {}}
    recorder.append(event(credentials, 2, "agent.registered", agent), credentials["session_token"])
    node = {"id": new_id(), "kind": "tool", "label": "Observed tool", "branch_id": "main",
            "provenance": {"mode": "recorded", "evidence": []}}
    definition = {"graph": copy.deepcopy(recorder.document["graph"]), "variables": {}, "interactive_questions": []}
    definition["graph"]["nodes"] = [node]
    mutation = event(credentials, 3, "definition.replaced", definition)
    mutation["expected_revision"] = recorder.document["graph"]["revision_id"]
    mutation["branch_id"] = "main"
    recorder.append(mutation, credentials["session_token"])
    return credentials, harness, agent, node


def running_attempt(recorder, credentials, harness, agent, node):
    return {"id": new_id(), "trace_id": new_id(), "node_id": node["id"],
            "revision_id": recorder.document["graph"]["revision_id"], "session_id": credentials["session_id"],
            "agent_id": agent["id"], "harness_id": harness["id"], "state": "running", "started_at": utc_now(),
            "inputs": {}, "outputs": {}, "artifact_ids": [], "provenance": {"mode": "recorded", "evidence": []}}


def test_no_consent_never_persists_content(recorder):
    credentials = session(recorder, False)
    private = "THIS_CONTENT_MUST_NOT_BE_RECORDED"
    message = event(credentials, 1, "message.recorded", {"role": "user", "content": private})
    with pytest.raises(ProtocolError, match="consent"):
        recorder.append(message, "not-authorized-credential")
    recorder.flush()
    assert private not in dumps(recorder.get_events())
    assert private not in (recorder.project / "aica.json").read_text()
    assert not recorder.document["agents"]
    assert not recorder.document["harnesses"]


def test_start_session_retry_returns_same_identity_and_credential(recorder):
    request_id = new_id()
    first = recorder.start_session(request_id, True)
    assert recorder.start_session(request_id, True) == first
    with pytest.raises(ProtocolError):
        recorder.start_session(request_id, False)
    assert len(recorder.document["sessions"]) == 1


def test_event_retry_is_durable_and_content_collision_is_rejected(recorder):
    credentials = session(recorder)
    message = event(credentials, 1, "message.recorded", {"role": "user", "content": "hello"})
    first = recorder.append(message, credentials["session_token"])
    duplicate = recorder.append(message, credentials["session_token"])
    assert first["commit_seq"] == duplicate["commit_seq"]
    assert duplicate["duplicate"]
    mutated = copy.deepcopy(message)
    mutated["payload"]["content"] = "different"
    with pytest.raises(ProtocolError, match="different content"):
        recorder.append(mutated, credentials["session_token"])
    assert len(recorder.records()) == 2


def test_late_source_events_fill_gaps_without_reordering_causality(recorder):
    credentials = session(recorder)
    second = event(credentials, 2, "message.recorded", {"role": "assistant", "content": "second"})
    first = event(credentials, 1, "message.recorded", {"role": "user", "content": "first"})
    second["causal_parents"] = [first["event_id"]]
    receipt = recorder.append(second, credentials["session_token"])
    assert not receipt["projected"]
    assert not recorder.document["snapshot"]["complete"]
    recorder.append(first, credentials["session_token"])
    assert recorder.document["snapshot"]["complete"]
    assert recorder.document["metadata"]["updated_at"] == second["timestamp"]


def test_concurrent_sources_preserve_all_events(recorder):
    credentials = session(recorder)
    messages = [event(credentials, sequence, "message.recorded", {"role": "tool", "content": f"{source}:{sequence}"},
                      source=f"source-{source}") for source in range(4) for sequence in range(1, 6)]
    with ThreadPoolExecutor(max_workers=8) as executor:
        receipts = list(executor.map(lambda message: recorder.append(message, credentials["session_token"]),
                                     reversed(messages)))
    assert len({receipt["commit_seq"] for receipt in receipts}) == 20
    assert len(recorder.records()) == 21
    assert recorder.document["snapshot"]["complete"]


def test_definition_race_has_one_winner(recorder):
    credentials = session(recorder)
    revision = recorder.document["graph"]["revision_id"]
    definition = {"graph": copy.deepcopy(recorder.document["graph"]), "variables": {}, "interactive_questions": []}
    first = event(credentials, 1, "definition.replaced", definition, "editor-one")
    second = event(credentials, 1, "definition.replaced", definition, "editor-two")
    first["expected_revision"] = revision
    second["expected_revision"] = revision
    first["branch_id"] = second["branch_id"] = "main"
    recorder.append(first, credentials["session_token"])
    with pytest.raises(ProtocolError) as failure:
        recorder.append(second, credentials["session_token"])
    assert failure.value.code == "revision_conflict"
    assert len(recorder.records()) == 2
    assert recorder.document["history"][0]["revision_id"] == revision


def test_variable_mutation_preserves_prior_definition_and_ordered_semantics(recorder):
    from pathlib import Path

    fixture_path = Path(__file__).parents[2] / "schema" / "tests" / "fixtures" / "branching.aica.json"
    fixture = loads(fixture_path.read_text())
    credentials = session(recorder)
    replacement = event(credentials, 1, "definition.replaced",
                        {key: fixture[key] for key in ("graph", "variables", "interactive_questions")})
    replacement["expected_revision"] = recorder.document["graph"]["revision_id"]
    replacement["branch_id"] = "main"
    first = recorder.append(replacement, credentials["session_token"])
    change = event(credentials, 2, "variables.changed", {"operations": [
        {"variable_id": "character", "mode": "override", "value": "cat"},
        {"variable_id": "character", "mode": "append", "value": "burger"},
        {"variable_id": "character", "mode": "keep"}]})
    change["expected_revision"] = first["revision_id"]
    change["branch_id"] = "main"
    second = recorder.append(change, credentials["session_token"])
    assert recorder.document["variables"]["character"]["value"] == "cat, burger"
    assert recorder.document["variables"]["character"]["default"] == "cyberpunk girl"
    assert first["revision_id"] != second["revision_id"]
    previous = next(item for item in recorder.document["history"] if item.get("revision_id") == first["revision_id"])
    artifact = next(item for item in recorder.document["artifacts"] if item["id"] == previous["artifact_id"])
    old_definition = loads((recorder.project / artifact["uri"]).read_text())
    assert old_definition["variables"]["character"]["value"] == "cyberpunk girl"


def test_pending_finish_resolves_when_start_arrives(recorder):
    credentials, harness, agent, node = setup_actor(recorder)
    run = running_attempt(recorder, credentials, harness, agent, node)
    finish = event(credentials, 1, "tool.finished", {"run_id": run["id"], "state": "succeeded",
                   "ended_at": utc_now(), "outputs": {}, "artifact_ids": []}, "finish-source", agent["id"])
    recorder.append(finish, credentials["session_token"])
    assert not recorder.document["snapshot"]["complete"]
    recorder.append(event(credentials, 4, "tool.started", run, agent_id=agent["id"]), credentials["session_token"])
    assert recorder.document["runs"][0]["state"] == "succeeded"
    assert recorder.document["snapshot"]["complete"]


def test_recovery_marks_unfinished_attempt_unknown_and_accepts_late_evidence(tmp_path):
    recorder = Recorder(tmp_path)
    credentials, harness, agent, node = setup_actor(recorder)
    run = running_attempt(recorder, credentials, harness, agent, node)
    start = event(credentials, 4, "tool.started", run, agent_id=agent["id"])
    recorder.append(start, credentials["session_token"])
    recorder.close()
    recovered = Recorder(tmp_path)
    try:
        assert recovered.document["runs"][0]["state"] == "unknown"
        assert recovered.append(start, credentials["session_token"])["duplicate"]
        finish = event(credentials, 5, "tool.finished", {"run_id": run["id"], "state": "succeeded",
                       "ended_at": utc_now(), "outputs": {}, "artifact_ids": []}, agent_id=agent["id"])
        recovered.append(finish, credentials["session_token"])
        assert recovered.document["runs"][0]["state"] == "succeeded"
    finally:
        recovered.close()


def test_pause_excludes_content_and_resume_is_explicit(recorder):
    credentials = session(recorder)
    recorder.append(event(credentials, 1, "session.paused", {}), credentials["session_token"])
    with pytest.raises(ProtocolError, match="not recording"):
        recorder.append(event(credentials, 2, "message.recorded", {"role": "user", "content": "excluded"}),
                        credentials["session_token"])
    recorder.append(event(credentials, 2, "session.resumed", {}), credentials["session_token"])
    recorder.append(event(credentials, 3, "message.recorded", {"role": "user", "content": "included"}),
                    credentials["session_token"])
    assert "excluded" not in dumps(recorder.get_events())
    assert "included" in dumps(recorder.get_events())


def test_child_agent_cannot_exceed_session_consent(recorder):
    credentials, harness, agent, node = setup_actor(recorder)
    child = {"id": new_id(), "session_id": credentials["session_id"], "parent_agent_id": agent["id"],
             "harness_id": harness["id"], "client": "test-child", "model": {}}
    with pytest.raises(ProtocolError, match="Invalid AiCA"):
        recorder.append(event(credentials, 4, "agent.registered", child), credentials["session_token"])


def test_artifact_retry_uses_original_bytes_even_if_source_changed(recorder):
    credentials = session(recorder)
    path = recorder.project / "output.txt"
    path.write_text("first")
    request = event(credentials, 1, "artifact.recorded", {})
    first = recorder.capture_artifact(credentials["session_token"], request, "output.txt", "text/plain")
    path.write_text("changed")
    second = recorder.capture_artifact(credentials["session_token"], request, "output.txt", "text/plain")
    assert second["duplicate"]
    assert first["artifact"] == second["artifact"]
    assert second["artifact"]["sha256"] == hashlib.sha256(b"first").hexdigest()


def test_paths_and_internal_credentials_cannot_be_captured(recorder):
    credentials = session(recorder)
    for name in ("../outside", ".aica/recording.sqlite3", str(recorder.project / "aica.json")):
        with pytest.raises(ProtocolError):
            recorder.capture_artifact(credentials["session_token"], event(credentials, 1, "artifact.recorded", {}),
                                      name, "application/octet-stream")


def test_external_edit_blocks_snapshot_overwrite_without_losing_events(recorder):
    path = recorder.project / "aica.json"
    external = path.read_text() + " "
    path.write_text(external)
    credentials = session(recorder)
    recorder.append(event(credentials, 1, "message.recorded", {"role": "user", "content": "durable"}),
                    credentials["session_token"])
    with pytest.raises(ProtocolError) as failure:
        recorder.flush()
    assert failure.value.code == "snapshot_conflict"
    assert path.read_text() == external
    assert "durable" in dumps(recorder.get_events())


def test_crash_during_snapshot_replace_recovers_from_committed_ledger(recorder, monkeypatch):
    credentials = session(recorder)
    recorder.append(event(credentials, 1, "message.recorded", {"role": "user", "content": "durable"}),
                    credentials["session_token"])
    original = (recorder.project / "aica.json").read_bytes()
    from aica_mcp import store

    real_write = store.atomic_write

    def interrupted(path, content):
        if path.name == "aica.json":
            raise OSError("Injected replacement failure")
        real_write(path, content)

    monkeypatch.setattr(store, "atomic_write", interrupted)
    with pytest.raises(OSError):
        recorder.flush()
    assert (recorder.project / "aica.json").read_bytes() == original
    project = recorder.project
    recorder.close()
    monkeypatch.setattr(store, "atomic_write", real_write)
    restored = Recorder(project)
    try:
        snapshot = loads((project / "aica.json").read_text())
        assert snapshot["snapshot"]["commit_seq"] == 2
        assert validate_archive(snapshot)["valid"]
    finally:
        restored.close()


def test_only_one_recorder_owns_project(recorder):
    another = ProjectLock(recorder.directory / "recorder.lock")
    with pytest.raises(ProtocolError) as failure:
        another.acquire()
    assert failure.value.code == "project_busy"


def test_exported_history_is_self_describing_and_contains_no_session_token(recorder):
    credentials = session(recorder)
    recorder.append(event(credentials, 1, "message.recorded", {"role": "user", "content": "hello"}),
                    credentials["session_token"])
    recorder.flush()
    archive = loads((recorder.project / "aica.json").read_text())
    assert credentials["session_token"] not in dumps(archive)
    histories = [item for item in archive["history"] if item["kind"] == "events"]
    assert histories
    artifacts = {item["id"]: item for item in archive["artifacts"]}
    for history in histories:
        content = (recorder.project / artifacts[history["artifact_id"]]["uri"]).read_bytes()
        assert credentials["session_token"].encode() not in content
        assert hashlib.sha256(content).hexdigest() == artifacts[history["artifact_id"]]["sha256"]


def test_event_input_mutation_cannot_change_committed_history(recorder):
    credentials = session(recorder)
    message = event(credentials, 1, "message.recorded", {"role": "user", "content": "original"})
    recorder.append(message, credentials["session_token"])
    message["payload"]["content"] = "changed externally"
    assert recorder.records()[-1]["event"]["payload"]["content"] == "original"
    assert "changed externally" not in dumps(recorder.get_events())


def test_source_order_and_explicit_causality_cannot_form_a_cycle(recorder):
    credentials = session(recorder)
    earlier = event(credentials, 1, "message.recorded", {"role": "user", "content": "one"})
    later = event(credentials, 2, "message.recorded", {"role": "user", "content": "two"})
    earlier["causal_parents"] = [later["event_id"]]
    recorder.append(earlier, credentials["session_token"])
    with pytest.raises(ProtocolError, match="cycle"):
        recorder.append(later, credentials["session_token"])


def test_first_snapshot_creation_can_recover_after_failed_publish(tmp_path, monkeypatch):
    from aica_mcp import store

    real_write = store.atomic_write

    def interrupted(path, content):
        if path.name == "aica.json":
            raise OSError("Injected initial snapshot failure")
        real_write(path, content)

    monkeypatch.setattr(store, "atomic_write", interrupted)
    recorder = Recorder(tmp_path)
    assert recorder.snapshot_error
    recorder.close()
    monkeypatch.setattr(store, "atomic_write", real_write)
    restored = Recorder(tmp_path)
    try:
        assert restored.snapshot_error is None
        assert validate_archive(loads((tmp_path / "aica.json").read_text()))["valid"]
    finally:
        restored.close()


def test_historical_execution_retains_original_definition_and_harness(recorder):
    credentials, harness, agent, node = setup_actor(recorder)
    run = running_attempt(recorder, credentials, harness, agent, node)
    old_revision = run["revision_id"]
    replacement = event(credentials, 4, "definition.replaced", {
        "graph": {**recorder.document["graph"], "nodes": []}, "variables": {}, "interactive_questions": []})
    replacement.update(expected_revision=old_revision, branch_id="main")
    recorder.append(replacement, credentials["session_token"])
    new_revision = recorder.document["graph"]["revision_id"]
    recorder.append(event(credentials, 5, "tool.started", run, agent_id=agent["id"]), credentials["session_token"])
    definition = recorder.get_revision(old_revision)["definition"]
    assert definition["graph"]["nodes"] == [node]
    assert recorder.document["runs"][0]["harness_id"] == harness["id"]
    assert recorder.document["runs"][0]["revision_id"] == old_revision
    assert recorder.document["graph"]["revision_id"] == new_revision
    assert recorder.get_revision(new_revision)["definition"]["graph"]["nodes"] == []
    forged_run = {**run, "id": new_id(), "node_id": "never-existed"}
    with pytest.raises(ProtocolError, match="historical execution"):
        recorder.append(event(credentials, 6, "tool.started", forged_run, agent_id=agent["id"]),
                        credentials["session_token"])
    changed_harness = {**harness, "config": {"instructions": "different"}}
    with pytest.raises(ProtocolError, match="different content"):
        recorder.append(event(credentials, 6, "harness.registered", changed_harness), credentials["session_token"])


def test_historical_definition_hash_is_checked_and_new_location_restores_access(recorder):
    credentials, harness, agent, node = setup_actor(recorder)
    revision = recorder.document["graph"]["parent_revision_id"]
    reference = next(item for item in recorder.document["history"] if item["revision_id"] == revision)
    artifact = next(item for item in recorder.document["artifacts"] if item["id"] == reference["artifact_id"])
    original = (recorder.project / artifact["uri"]).read_bytes()
    (recorder.project / artifact["uri"]).write_bytes(b"corrupted")
    with pytest.raises(ProtocolError) as failure:
        recorder.get_revision(revision)
    assert failure.value.code == "artifact_unavailable"
    assert failure.value.details[0]["state"] == "mismatch"
    assert validate_archive(recorder.read_archive()["archive"])["valid"]
    (recorder.project / "relocated.json").write_bytes(original)
    location = event(credentials, 4, "artifact.location_added", {
        "artifact_id": artifact["id"], "sha256": artifact["sha256"], "uri": "relocated.json"})
    recorder.append(location, credentials["session_token"])
    assert recorder.get_revision(revision)["definition"]["graph"]["nodes"] == []
    assert artifact["uri"] != "relocated.json"
    assert recorder.document["graph"]["nodes"] == [node]


def test_location_event_preserves_content_and_origin_with_durable_retries(recorder):
    credentials = session(recorder)
    (recorder.project / "image.txt").write_text("same bytes")
    artifact = recorder.capture_artifact(credentials["session_token"], event(credentials, 1, "artifact.recorded", {}),
                                         "image.txt", "text/plain")["artifact"]
    (recorder.project / "moved.txt").write_text("same bytes")
    (recorder.project / artifact["uri"]).unlink()
    location = event(credentials, 2, "artifact.location_added", {
        "artifact_id": artifact["id"], "sha256": artifact["sha256"], "uri": "moved.txt"})
    first = recorder.append(location, credentials["session_token"])
    assert recorder.append(location, credentials["session_token"])["duplicate"]
    report = recorder.check_references()["references"][0]
    assert report["state"] == "verified"
    assert [item["state"] for item in report["locations"]] == ["missing", "verified"]
    stored = recorder.document["artifacts"][0]
    assert {key: value for key, value in stored.items() if key != "locations"} == artifact
    assert len(stored["locations"]) == 1
    assert first["commit_seq"] == recorder.append(location, credentials["session_token"])["commit_seq"]
    forged = event(credentials, 3, "artifact.location_added", {
        "artifact_id": artifact["id"], "sha256": "0" * 64, "uri": "other.txt"})
    with pytest.raises(ProtocolError, match="identity"):
        recorder.append(forged, credentials["session_token"])
    recorder.append(event(credentials, 3, "artifact.recorded", artifact), credentials["session_token"])
    recorder.close()
    recovered = Recorder(recorder.project)
    try:
        assert recovered.document["artifacts"][0] == stored
        assert recovered.append(location, credentials["session_token"])["duplicate"]
    finally:
        recovered.close()


def test_reference_checks_distinguish_readability_from_execution_and_declared_availability(recorder):
    credentials = session(recorder)
    for sequence, name in enumerate(("ok", "missing", "damaged", "remote", "unsafe", "unhashed"), 1):
        artifact = recorder.put_bytes(name.encode(), "text/plain", name)
        if name == "missing":
            (recorder.project / artifact["uri"]).unlink()
        elif name == "damaged":
            (recorder.project / artifact["uri"]).write_bytes(b"different")
        elif name == "remote":
            artifact["uri"] = "https://example.invalid/private"
        elif name == "unsafe":
            artifact["uri"] = ".aica/recording.sqlite3"
        elif name == "unhashed":
            artifact.update(sha256=None, availability="unverified")
        else:
            artifact["availability"] = "missing"
        recorder.append(event(credentials, sequence, "artifact.recorded", artifact), credentials["session_token"])
    before = copy.deepcopy(recorder.document)
    report = recorder.check_references()
    assert report["archive_readable"] and not report["execution_checked"]
    assert not report["all_references_verified"]
    assert [item["state"] for item in report["references"]] == [
        "verified", "missing", "mismatch", "unverified", "blocked", "unverified"]
    assert report["references"][0]["declared_availability"] == "missing"
    assert recorder.document == before


def test_metadata_pagination_detects_new_events_and_new_history_segments(recorder):
    credentials = session(recorder)
    for sequence in range(1, 4):
        artifact = recorder.put_bytes(str(sequence).encode(), "text/plain")
        recorder.append(event(credentials, sequence, "artifact.recorded", artifact), credentials["session_token"])
    first = recorder.get_items("artifacts", limit=2)
    second = recorder.get_items("artifacts", offset=first["next_offset"], limit=2,
                                snapshot_id=first["snapshot"]["snapshot_id"])
    assert len(first["items"] + second["items"]) == first["total"] == 3
    assert second["next_offset"] is None
    assert recorder.check_references(limit=1)["all_references_verified"] is None
    with pytest.raises(ProtocolError, match="require snapshot_id"):
        recorder.get_items("artifacts", offset=2)
    recorder.flush()
    with pytest.raises(ProtocolError, match="pagination"):
        recorder.get_items("artifacts", offset=2, snapshot_id=first["snapshot"]["snapshot_id"])
    current = recorder.get_items("nodes")
    assert not current["items"]
    recorder.append(event(credentials, 4, "note.recorded", {"text": "new", "provenance": {
        "mode": "recorded", "evidence": []}}), credentials["session_token"])
    with pytest.raises(ProtocolError, match="pagination"):
        recorder.get_items("nodes", snapshot_id=current["snapshot"]["snapshot_id"])


@pytest.mark.parametrize("damage", ["producer", "cache"])
def test_execution_artifact_references_reject_semantic_mismatch(recorder, damage):
    credentials, harness, agent, node = setup_actor(recorder)
    run = running_attempt(recorder, credentials, harness, agent, node)
    recorder.append(event(credentials, 4, "tool.started", run, agent_id=agent["id"]), credentials["session_token"])
    archive = recorder.read_archive()["archive"]
    assert validate_archive(archive)["valid"]
    if damage == "producer":
        artifact = recorder.put_bytes(b"output", "text/plain")
        artifact.update(producer_run_id=run["id"], producer_node_id="wrong-node")
        archive["artifacts"].append(artifact)
    else:
        archive["runs"][0]["cache"] = {"artifact_ids": ["unknown-artifact"]}
    assert not validate_archive(archive)["valid"]


def test_missing_script_is_reported_with_affected_node_without_breaking_archive_reading(recorder):
    credentials, harness, agent, node = setup_actor(recorder)
    (recorder.project / "script.py").write_text('print("example")')
    artifact = recorder.capture_artifact(credentials["session_token"], event(credentials, 4, "artifact.recorded", {}),
                                         "script.py", "text/x-python")["artifact"]
    definition = recorder.get_revision(recorder.document["graph"]["revision_id"])["definition"]
    definition["graph"]["nodes"][0]["execution"] = {
        "kind": "script", "tool": "python3", "version": "3.12", "params": {}, "outputs": [],
        "runtime_ref": "demo-python@1", "argv": ["python3", "script.py"], "script_artifact_id": artifact["id"],
        "inputs": {"script": {"kind": "artifact", "artifact_id": artifact["id"]}}}
    replacement = event(credentials, 5, "definition.replaced", {
        key: definition[key] for key in ("graph", "variables", "interactive_questions")})
    replacement.update(expected_revision=recorder.document["graph"]["revision_id"], branch_id="main")
    recorder.append(replacement, credentials["session_token"])
    (recorder.project / artifact["uri"]).unlink()
    report = recorder.check_references()
    reference = next(item for item in report["references"] if item["artifact_id"] == artifact["id"])
    assert reference["state"] == "missing"
    assert {item["kind"] for item in reference["uses"]} == {"node_input", "node_script"}
    assert all(item["node_id"] == node["id"] for item in reference["uses"])
    assert report["archive_readable"] and not report["execution_checked"]


@pytest.mark.parametrize("uri", ["../outside", "/etc/hosts", "..\\outside", ".aica/endpoint.json"])
def test_reference_inspection_does_not_read_unsafe_paths(recorder, uri):
    artifact = {"id": "unsafe", "uri": uri, "sha256": "a" * 64, "availability": "unverified"}
    report = inspect_artifact(recorder.project, artifact)
    assert report["state"] == "blocked"


def test_reference_inspection_blocks_symlink_escape_and_checks_size(recorder, tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside.txt")
    outside.write_text("outside")
    try:
        (recorder.project / "escape").symlink_to(outside)
        artifact = {"id": "escape", "uri": "escape", "sha256": hashlib.sha256(b"outside").hexdigest(),
                    "availability": "available"}
        assert inspect_artifact(recorder.project, artifact)["state"] == "blocked"
    finally:
        outside.unlink()
    artifact = recorder.put_bytes(b"inside", "text/plain")
    artifact["size"] += 1
    assert inspect_artifact(recorder.project, artifact)["state"] == "mismatch"
