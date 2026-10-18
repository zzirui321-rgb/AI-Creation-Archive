"""Generic A/D platform assembly. No session template or client-supplied plan."""

from aica_gateway.app import create_app as platform_app
from aica_runtime.executors import default_registry

from .runtime_bridge import register_preview_tools


def create_app(root, tokens, settings=None):
    return platform_app(root, tokens=tokens, registry=register_preview_tools(default_registry(), settings))
