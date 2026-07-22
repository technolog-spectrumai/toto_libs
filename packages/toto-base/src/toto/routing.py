"""Channels routing helpers for host ASGI applications.

Replaces the per-app try/except import blocks hosts used to carry: each
routing module is imported defensively, in a stable order, and missing
optional apps are skipped exactly as before.
"""
import importlib

# The library's own websocket apps, in the historical collection order. A host
# that owns further websocket apps (zenobia carries texlab and sketch) passes an
# explicit list — see zenobia/zenobia/asgi.py and faros/faros/asgi.py.
DEFAULT_WEBSOCKET_ROUTING_MODULES = [
    "toto.forum.routing",
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
