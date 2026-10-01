"""uvicorn's own log lines keep no query string (2026-10-01).

uvicorn writes a line for every socket it accepts or refuses (``uvicorn.error``,
at INFO) and for every HTTP request (``uvicorn.access``), and each names the
path WITH its query string::

    10.0.0.4:51234 - "WebSocket /ws/forum/hall/?token=<session key>" [accepted]

Today's desktop clients still sign their socket in with ``?token=``, so that
line kept a live credential in the web container's log, which promtail ships
to Loki. The query string is never what such a line is read for, and it is
where a credential travels when a client cannot send a header (the socket's
key, the e-mail change link's token, an OIDC sign-in's code) and where a
search names what a member looked for. nginx's access log drops it since the
same day (deploy.py's ``toto_noquery`` format); this filter drops it from
uvicorn's lines, which carry the path as one argument of the record.

``TokenAuthMiddleware`` puts the filter on when a host builds its socket
stack, so every process that serves the token door has it.
"""

import logging

#: The loggers uvicorn names a request's path on.
UVICORN_LOGGERS = ("uvicorn.error", "uvicorn.access")


class NoQueryString(logging.Filter):
    """Cuts every path argument of a record at its ``?``."""

    def filter(self, record):
        if isinstance(record.args, tuple) and record.args:
            record.args = tuple(_path_only(arg) for arg in record.args)
        return True


def _path_only(arg):
    # uvicorn quotes the path before it adds the query, so the first "?" in
    # the argument is where the query begins.
    if isinstance(arg, str) and arg.startswith("/") and "?" in arg:
        return arg.split("?", 1)[0]
    return arg


def keep_no_query_string():
    """Put the filter on uvicorn's loggers, once per process."""
    for name in UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(f, NoQueryString) for f in logger.filters):
            logger.addFilter(NoQueryString())
