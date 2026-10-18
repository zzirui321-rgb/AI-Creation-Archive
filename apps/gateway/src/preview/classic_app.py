"""Local assembly of D's archive API and B's development preview routes."""

from pathlib import Path

from .integration_server import create_app as create_preview_app


def create_classic_app(data_dir: Path, token: str):
    return create_preview_app(data_dir / "platform", {"local-developer": token})
