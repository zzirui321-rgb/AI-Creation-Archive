import pytest
from aica_gateway.app import create_app
from aica_schema.protocol import ProtocolError
from fastapi.testclient import TestClient


def test_static_frontend_does_not_shadow_api_or_expose_data(tmp_path, monkeypatch):
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<html>AiCA deployment fixture</html>", encoding="utf-8")
    (web / "assets" / "app.js").write_text("console.log('fixture')", encoding="utf-8")
    monkeypatch.setenv("AICA_WEB_DIR", str(web))
    with TestClient(create_app(tmp_path / "private-data", tokens={})) as client:
        assert "AiCA deployment fixture" in client.get("/").text
        assert client.get("/assets/app.js").status_code == 200
        assert client.get("/health").json()["service"] == "aica-gateway"
        assert client.get("/api/projects").status_code == 401
        assert client.get("/api/repos").json()["repositories"] == []
        assert client.get("/platform.sqlite3").status_code == 404
        assert client.get("/private-data/platform.sqlite3").status_code == 404
        assert client.get("/api/not-a-route").status_code == 404


def test_static_mount_requires_a_built_frontend(tmp_path, monkeypatch):
    monkeypatch.setenv("AICA_WEB_DIR", str(tmp_path))
    with pytest.raises(ProtocolError, match="built frontend"):
        create_app(tmp_path / "data", tokens={})
