"""Turn a half-configured deployment into a `manage.py check` message.

Every one of these is a WARNING rather than an ERROR on purpose: booking is
arithmetic that works with no manager at all, so a host that installs anastasia
without deploying the manager is *reduced*, not *broken*, and must still boot —
the same judgement `aralia/checks.py` makes about BUILD_WEASYPRINT.
"""

from __future__ import annotations

import os

from django.core.checks import Warning, register

from . import conf


@register()
def check_anastasia(app_configs, **kwargs):
    messages = []

    if not conf.pool_is_configured():
        messages.append(Warning(
            "ANASTASIA_POOL is unset, so no Compute Capsule can be reserved.",
            hint="Set ANASTASIA_POOL = {'cpu_millicores': …, 'ram_mb': …, "
                 "'scratch_mb': …, 'pids': …} to the capacity this machine may "
                 "hand out. Until then the Capsule page refuses every reservation.",
            id="anastasia.W001",
        ))

    backend = conf.runtime_backend_path()
    is_null = backend.endswith("NullRuntimeBackend")
    socket_path = conf.executor_socket()
    if is_null and socket_path:
        messages.append(Warning(
            "ANASTASIA_EXECUTOR_SOCKET is set but the runtime backend is still "
            "the null one, so Capsules cannot be mounted.",
            hint="Set ANASTASIA_RUNTIME_BACKEND to "
                 "toto.anastasia.executor_backend.ExecutorRuntimeBackend.",
            id="anastasia.W002",
        ))
    if not is_null and not socket_path:
        messages.append(Warning(
            "A live Anastasia runtime backend is configured with no "
            "ANASTASIA_EXECUTOR_SOCKET to talk to.",
            hint="Set ANASTASIA_EXECUTOR_SOCKET to the executor's socket "
                 "(deploy.py writes /run/anastasia/executord.sock).",
            id="anastasia.W003",
        ))
    if not is_null and not conf.shared_secret():
        messages.append(Warning(
            "The Anastasia executor is configured without a shared secret, so "
            "requests to it cannot be signed.",
            hint="Set ANASTASIA_SHARED_SECRET (deploy.py generates one into "
                 "the stack .env).",
            id="anastasia.W004",
        ))
    # The socket is a FILE, so its absence is checkable — unlike a URL, which
    # could only ever have been probed by connecting. A path that is configured
    # and not there means the unit is not running or its directory was not
    # mounted into this container, and both are operator-fixable before anybody
    # reserves anything.
    #
    # A WARNING rather than an ERROR on purpose: `manage.py check` runs in
    # build containers and on developer laptops where no executor exists, and a
    # check that fails the build there would be switched off rather than fixed.
    if not is_null and socket_path and not os.path.exists(socket_path):
        messages.append(Warning(
            f"The Anastasia executor's socket is not there ({socket_path}), so "
            "every mount will refuse.",
            hint="Start anastasia-executord on the host, and make sure the "
                 "container mounts its DIRECTORY (/run/anastasia) rather than "
                 "the socket file — a bind of the file pins a dead inode "
                 "across an executor restart.",
            id="anastasia.W005",
        ))
    return messages
