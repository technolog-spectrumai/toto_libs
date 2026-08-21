"""Turn a half-configured deployment into a `manage.py check` message.

Every one of these is a WARNING rather than an ERROR on purpose: booking is
arithmetic that works with no manager at all, so a host that installs anastasia
without deploying the manager is *reduced*, not *broken*, and must still boot —
the same judgement `aralia/checks.py` makes about BUILD_WEASYPRINT.
"""

from __future__ import annotations

from django.core.checks import Warning, register

from . import conf


@register()
def check_anastasia(app_configs, **kwargs):
    messages = []

    if not conf.pool_is_configured():
        messages.append(Warning(
            "ANASTASIA_POOL is unset, so no Compute Gear can be reserved.",
            hint="Set ANASTASIA_POOL = {'cpu_millicores': …, 'ram_mb': …, "
                 "'scratch_mb': …, 'pids': …} to the capacity this machine may "
                 "hand out. Until then the Gear page refuses every reservation.",
            id="anastasia.W001",
        ))

    backend = conf.runtime_backend_path()
    is_null = backend.endswith("NullRuntimeBackend")
    if is_null and conf.manager_url():
        messages.append(Warning(
            "ANASTASIA_MANAGER_URL is set but the runtime backend is still the "
            "null one, so Gears cannot be mounted.",
            hint="Set ANASTASIA_RUNTIME_BACKEND to the manager-backed class.",
            id="anastasia.W002",
        ))
    if not is_null and not conf.manager_url():
        messages.append(Warning(
            "A live Anastasia runtime backend is configured with no "
            "ANASTASIA_MANAGER_URL to talk to.",
            hint="Set ANASTASIA_MANAGER_URL to where anastasia_manager listens.",
            id="anastasia.W003",
        ))
    if not is_null and not conf.shared_secret():
        messages.append(Warning(
            "The Anastasia manager is configured without a shared secret, so "
            "requests to it cannot be signed.",
            hint="Set ANASTASIA_SHARED_SECRET (deploy.py generates one into "
                 "the stack .env).",
            id="anastasia.W004",
        ))
    return messages
