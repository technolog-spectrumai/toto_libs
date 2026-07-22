"""Channels routing helpers for host ASGI applications.

Replaces the per-app try/except import blocks hosts used to carry: each
routing module is imported defensively, in a stable order, and missing
optional apps are skipped exactly as before.
"""
import importlib

# Portal's historical collection order. faros passes its own shorter list.
DEFAULT_WEBSOCKET_ROUTING_MODULES = [
    "toto.forum.routing",
    "toto.texlab.routing",
    "toto.antaresia.routing",
    "toto.editor.routing",
    "toto.sabbia.routing",
]


def collect_websocket_urlpatterns(modules=None):
    """Concatenate ``websocket_urlpatterns`` from each importable module.

    Mirrors the semantics of the old per-app ``try: from toto.<app>.routing
    import websocket_urlpatterns ... except ImportError: pass`` blocks.
    """
    patterns = []
    for dotted in DEFAULT_WEBSOCKET_ROUTING_MODULES if modules is None else modules:
        try:
            module = importlib.import_module(dotted)
            patterns += module.websocket_urlpatterns
        except (ImportError, AttributeError):
            continue
    return patterns
