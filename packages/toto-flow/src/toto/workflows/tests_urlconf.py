"""A urlconf for this package's tests, so they stop depending on the host.

A wheel's tests must not assume a host mounted the wheel. zenobia installs
``toto.workflows`` as its job runner — the antivirus queues scans through it and
weather loads through it — and deliberately does **not** mount its routes: the
app is infrastructure there, not a feature offered to users. That is a
legitimate host choice, and it broke twelve tests in this package with
``'workflows' is not a registered namespace`` — tests about ownership,
attribution and permissions, none of which is about routing.

So the tests bring their own: whatever this host mounts, **plus** this app.
Taking the host's patterns rather than replacing them matters, because the UI
tests render real templates and the shared page furniture reverses ``core:``
and ``sso:`` names through them; a bare urlconf holding only this app would
trade one ``NoReverseMatch`` for another.

## Why the host urlconf is read off the settings *module*

Not ``settings.ROOT_URLCONF``. By the time this module is imported,
``@override_settings`` has already repointed that name **at this file**, so
reading it imports us into ourselves — a circular import that surfaces as
``partially initialized module … has no attribute 'urlpatterns'``.
``DJANGO_SETTINGS_MODULE`` names the host's settings module and is a plain
environment variable, so its ``ROOT_URLCONF`` is still the real one. Note that
``settings.SETTINGS_MODULE`` is **not** usable for this: under an override it is
``None``, because Django swaps the whole settings object for a
``UserSettingsHolder`` that never carried the name.

Nothing here hard-codes a host, so the module works unchanged on every one. On
a host that already mounts this app at the same prefix the extra include is a
no-op: both entries resolve through ``toto.workflows.urls`` to the same views
at the same paths.
"""

import os
from importlib import import_module

from django.urls import include, path

_host = import_module(os.environ["DJANGO_SETTINGS_MODULE"])
_host_urlpatterns = list(import_module(_host.ROOT_URLCONF).urlpatterns)

urlpatterns = _host_urlpatterns + [
    path("workflows/", include(("toto.workflows.urls", "workflows"), namespace="workflows")),
]
