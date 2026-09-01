"""The audit trail's public surface: ``record``, ``change`` and ``event``.

Deferred imports, so importing this package does not import models and this
module stays safe to reference from ``settings``-adjacent code.
"""


def record(*args, **kwargs):
    from toto.audit.services import record as service_record

    return service_record(*args, **kwargs)


def change(*args, **kwargs):
    from toto.audit.services import change as service_change

    return service_change(*args, **kwargs)


def event(*args, **kwargs):
    from toto.audit.services import event as service_event

    return service_event(*args, **kwargs)


def system_actor():
    """The SYSTEM sentinel for record(actor_user=...) — an event with no
    actor at all, as opposed to None's "use the ambient request's user"."""
    from toto.audit.services import SYSTEM

    return SYSTEM


__all__ = ["change", "event", "record", "system_actor"]
