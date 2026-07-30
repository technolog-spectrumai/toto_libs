"""Route the whole ORM to whichever side of the link is currently talking.

A contextvar rather than a thread local so it behaves under async views too, and a
single ContextVar rather than one per model because the switch is "which instance am I
right now", not "where does this table live".
"""
import contextlib
import contextvars

_side = contextvars.ContextVar("datalink_side", default="default")


@contextlib.contextmanager
def use_peer_db():
    """Read and write as the peer for the duration."""
    token = _side.set("peer")
    try:
        yield
    finally:
        _side.reset(token)


def current_side() -> str:
    return _side.get()


class PeerRouter:
    """During a peer hop the whole ORM IS the peer's."""

    def db_for_read(self, model, **hints):
        return _side.get()

    def db_for_write(self, model, **hints):
        # Following the contextvar on writes too is what makes the peer's own
        # read_count land in the peer's database — which is the assertion that proves
        # the two sides really are separate.
        return _side.get()

    def allow_relation(self, obj1, obj2, **hints):
        # The schema is identical on both aliases and no relation ever spans them;
        # Django refuses cross-alias relations unless told otherwise.
        return True

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        # Both databases get every table, so either side can play either role.
        return True
