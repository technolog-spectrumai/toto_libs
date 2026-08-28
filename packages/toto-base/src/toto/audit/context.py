"""The ambient actor, so a service deep in a call stack need not be handed one.

Copied from the parked Django Irena's ``toto.audit.context``. ``ContextVar``
rather than thread-local because it is correct under async views as well, and
Django's ASGI path would silently share a thread-local between requests.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class AuditContext:
    user: object | None = None
    request: object | None = None
    source: str = "system"
    correlation_id: str = ""


_context = ContextVar("placidia_audit_context", default=None)
_suppressed = ContextVar("placidia_audit_suppressed", default=False)


def current_context():
    return _context.get()


def set_context(value):
    return _context.set(value)


def reset_context(token):
    _context.reset(token)


def is_suppressed():
    return _suppressed.get()


@contextmanager
def suppress_audit():
    """Turn the chain off for a block — bulk loaders and fixtures only.

    Deliberately not exposed through a setting: a platform whose audit can be
    disabled by configuration has no audit.
    """
    token = _suppressed.set(True)
    try:
        yield
    finally:
        _suppressed.reset(token)
