import sqlite3
from threading import Event
from time import monotonic, sleep

from aica_schema.protocol import new_id
from test_graph_api import graph_environment as graph_environment
from test_graph_api import import_source, wait_parse
from test_platform_api import login


def test_parse_autosaves_private_versions_and_recovers_without_model_retry(graph_environment, tmp_path, monkeypatch):
    app, client, calls, _, _, _, _ = graph_environment
    owner = login(client, "autosave-owner")
    client.headers.update(owner)
    url, payload, _, _ = import_source(client, tmp_path)

    def parse():
        response = client.post(url + "/semantic-parses", json={**payload, "request_id": new_id()})
        assert response.status_code == 202
        return wait_parse(client, url, response.json()["parse"]["id"])

    first = parse()
    deadline = monotonic() + 3
    while first["publication"] is None and monotonic() < deadline:
        sleep(.01)
        first = client.get(url + "/semantic-parses/" + first["id"]).json()["parse"]
    assert first["state"] == "succeeded" and first["publication"]
    publication = first["publication"]
    repo = "/api/repos/" + publication["repo_id"]
    assert client.get(repo).json()["repository"]["visibility"] == "private"
    document = client.get(repo + "/graph").json()["document"]
    assert document["graph_id"] == first["graph_id"]
    endpoint = url + "/semantic-parses/" + first["id"] + "/repository"
    for _ in range(2):
        assert client.post(endpoint).json()["publication"] == publication
    assert len(client.get(repo + "/commits").json()["commits"]) == 2
    second = parse()
    second_saved = client.post(url + "/semantic-parses/" + second["id"] + "/repository").json()["publication"]
    assert second_saved["repo_id"] == publication["repo_id"]
    assert second_saved["commit_id"] != publication["commit_id"]
    assert len(client.get(repo + "/commits").json()["commits"]) == 3
    original = app.state.platform.publish_parse
    attempted = Event()

    def fail_save(*_):
        attempted.set()
        raise sqlite3.OperationalError()

    monkeypatch.setattr(app.state.platform, "publish_parse", fail_save)
    third = parse()
    assert attempted.wait(3)
    assert third["state"] == "succeeded" and third["publication"] is None
    monkeypatch.setattr(app.state.platform, "publish_parse", original)
    repair = client.post(url + "/semantic-parses/" + third["id"] + "/repository")
    assert repair.status_code == 200
    assert len(calls) == 3
    head = client.get(repo + "/graph").json()["commit"]["id"]
    client.patch(repo + "/visibility", json={"visibility": "public"}).raise_for_status()
    fourth = parse()
    new_private = client.post(url + "/semantic-parses/" + fourth["id"] + "/repository").json()["publication"]
    assert new_private["repo_id"] == publication["repo_id"]
    assert client.get(repo + "/graph").json()["commit"]["id"] != head
    private_repo = "/api/repos/" + new_private["repo_id"]
    assert client.get(private_repo).json()["repository"]["visibility"] == "private"
    other = login(client, "autosave-other")
    assert client.post(endpoint, headers=other).status_code == 404
    assert client.get(private_repo, headers=other).status_code == 404
