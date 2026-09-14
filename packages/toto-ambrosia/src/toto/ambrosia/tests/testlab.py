"""The language app ambrosia's own tests run on: a vehicle, not a lab.

Ambrosia provides the lobby, the room and the JSON endpoints behind it
(`urls.workspace_urlpatterns()`) but mounts none of them. A language app
embeds them under its own namespace and registers into `registry.py` from its
`ready()`. Until 2026-09-14 this suite borrowed the real ones: it reversed
`dracena:` and `texlab:` names and read dracena's settings declaration. Both
labs were parked that day (zenobia/limbo/dracena, zenobia/limbo/texlab), which
left every generic page here without a subject.

This is the smallest thing that registers the way they did, so the base is
tested against the SEAM rather than against whichever lab a host installs:

* `NAMESPACE` is the URL namespace and the settings section;
* `fields()` is a declaration of its own, built the way the labs built theirs:
  the Capsule field first where there are Capsules, then one bounded knob;
* `install(testcase, **hooks)` registers it for ONE test and restores the
  registry afterwards;
* `tests/urls.py` is the host's urlconf plus these routes, applied by
  `base.TestlabTestCase`.

It is never registered at import. On a host that installs a real lab for the
same kind, that lab's entry must be back when the test ends.
"""

from __future__ import annotations

import dataclasses

from django.conf import settings
from django.urls import reverse

from toto.ambrosia import capsules, registry
from toto.ambrosia.models import WorkspaceKind
from toto.ambrosia.settings_spec import SECONDS, Field

#: The URL namespace, and the key of this lab's section in `Workspace.settings`.
NAMESPACE = "ambrosia_testlab"

#: The urlconf that mounts the test lab. See tests/urls.py.
URLCONF = "toto.ambrosia.tests.urls"

#: The host's own urlconf, read when the test modules are imported, before
#: any test overrides ROOT_URLCONF.
HOST_URLCONF = settings.ROOT_URLCONF

#: Bounds of the one knob the test lab declares.
TIMEOUT_MIN, TIMEOUT_MAX = 5, 600


def fields() -> tuple[Field, ...]:
    capsule = capsules.field()
    return ((capsule,) if capsule else ()) + (
        Field(
            key="timeout",
            kind=SECONDS,
            label="Timeout",
            default=None,
            minimum=TIMEOUT_MIN,
            maximum=TIMEOUT_MAX,
            needs_execute=True,
            help_text="Declared by toto.ambrosia's own tests.",
        ),
    )


def app(**hooks) -> registry.WorkspaceApp:
    """The test lab's registry entry, with any of its hooks replaced.

    `dataclasses.replace` rather than a hand-built copy. The copies this suite
    used to make listed the fields one by one, and the ones made to swap
    `snapshot` silently dropped `run` and `poll`.
    """
    return dataclasses.replace(registry.WorkspaceApp(
        namespace=NAMESPACE,
        kind=WorkspaceKind.PYTHON,
        extra_context=lambda request, workspace: {},
        extra_urls=lambda namespace, workspace: {},
        teardown=lambda workspace: None,
        main_id_for=lambda workspace: None,
        settings_fields=fields,
    ), **hooks)


_ABSENT = object()


def install(testcase, **hooks) -> registry.WorkspaceApp:
    """Register the test lab for one test and restore the registry after it.

    Both tables go back to exactly what they held, including nothing. A leak
    would not stay in this suite: toto.repo asks the same registry which room a
    workspace opens in, and would start reversing a namespace no host mounts.
    Installing again inside a test (to swap a hook) stacks correctly, because
    cleanups run last-in, first-out.
    """
    lab = app(**hooks)
    for table, key in ((registry._BY_NAMESPACE, lab.namespace),
                       (registry._BY_KIND, lab.kind)):
        testcase.addCleanup(_restore, table, key, table.get(key, _ABSENT))
    registry.register(lab)
    return lab


def _restore(table, key, previous):
    if previous is _ABSENT:
        table.pop(key, None)
    else:
        table[key] = previous


def url(name: str, *args, **kwargs) -> str:
    """A route of the test lab's, by its name in `workspace_urlpatterns()`."""
    return reverse(f"{NAMESPACE}:{name}", args=args or None,
                   kwargs=kwargs or None)
