"""Where the live socket is, for code that must not import channels."""

#: The one WebSocket endpoint, as a URL path without its leading slash.
WS_PATH = "ws/live/"


def socket_path() -> str:
    """The address a page opens (``/ws/live/``), or "" on a host that serves
    no socket: no channel layer, so nothing would ever be sent on one."""
    from toto.core import live

    return "/" + WS_PATH if live.available() else ""
