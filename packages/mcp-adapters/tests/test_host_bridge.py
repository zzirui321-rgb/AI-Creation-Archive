import json
from pathlib import Path

import pytest
from aica_mcp import host_bridge
from aica_schema.protocol import ProtocolError, new_id


class HostSocket:
    def __init__(self, project, bridge, error=False):
        self.sent = []
        self.replies = [json.dumps({'id': 1, 'result': {}}), json.dumps({'id': 2, 'result': {
            'isError': error, 'structuredContent': {'status': 'success', 'project': str(project),
            'client': 'codex', 'host_routing_version': 1, 'bridge_id': bridge}}})]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def send(self, value):
        self.sent.append(json.loads(value))

    def recv(self, timeout):
        return self.replies.pop(0)


@pytest.mark.parametrize('damage', [None, 'workspace', 'tool_error'])
def test_probe_uses_exact_host_thread_and_rejects_foreign_workspace(tmp_path, monkeypatch, damage):
    session = new_id()
    bridge = new_id()
    socket = HostSocket(tmp_path.parent if damage == 'workspace' else tmp_path, bridge, damage == 'tool_error')
    monkeypatch.setattr('websockets.sync.client.unix_connect', lambda *args, **kwargs: socket)
    if damage:
        with pytest.raises(ProtocolError):
            host_bridge.probe_bridge(Path('/fixture/socket'), tmp_path, session, 2)
    else:
        assert host_bridge.probe_bridge(Path('/fixture/socket'), tmp_path, session, 2) == bridge
    assert socket.sent[-1]['params'] == {'threadId': session, 'server': 'aica',
                                         'tool': 'aica_host_status', 'arguments': {}}
    assert all('prompt' not in json.dumps(item) for item in socket.sent)


def test_multiple_hosts_with_different_connections_are_not_guessed(tmp_path, monkeypatch):
    monkeypatch.setattr(host_bridge, 'daemon_sockets', lambda: [Path('/one'), Path('/two')])
    monkeypatch.setattr(host_bridge, 'probe_bridge', lambda path, *args: str(path))
    with pytest.raises(ProtocolError) as error:
        host_bridge.discover_bridge(tmp_path, new_id())
    assert error.value.code == 'bridge_ambiguous'


def test_unavailable_host_retains_legacy_pid_matching(tmp_path, monkeypatch):
    monkeypatch.setattr(host_bridge, 'daemon_sockets', lambda: [Path('/one')])

    def unavailable(*args):
        raise OSError('Fixture Host unavailable')

    monkeypatch.setattr(host_bridge, 'probe_bridge', unavailable)
    assert host_bridge.discover_bridge(tmp_path, new_id()) is None


def test_foreign_workspace_does_not_fall_back_to_pid_matching(tmp_path, monkeypatch):
    monkeypatch.setattr(host_bridge, 'daemon_sockets', lambda: [Path('/one')])

    def foreign(*args):
        raise ProtocolError('Different workspace', 'workspace_mismatch')

    monkeypatch.setattr(host_bridge, 'probe_bridge', foreign)
    with pytest.raises(ProtocolError) as error:
        host_bridge.discover_bridge(tmp_path, new_id())
    assert error.value.code == 'workspace_mismatch'
