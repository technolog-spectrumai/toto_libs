"""System checks — the dependencies cyprian cannot discover for itself.

Cyprian is a host app and reuses two library apps directly: `toto.memo` for the
HTML/SVG/KaTeX allowlists and the editor's drag, history and sanitise modules,
and `toto.verbena` for the page/section vocabulary it serialises.

Neither is optional and neither is checkable at import time in a useful way — a
missing one surfaces as a 500 on the first save, long after the build. A system
check turns it into a line of output at `manage.py check`, which is where a
build problem belongs.
"""

from django.apps import apps
from django.core.checks import Error, register


@register()
def cyprian_dependencies(app_configs, **kwargs):
    errors = []


    if not apps.is_installed("toto.antivirus"):
        errors.append(Error(
            "Cyprian needs toto.antivirus.",
            hint=("The writer's sanitisers live in toto.antivirus.sanitize since "
                  "8/2026. This is a HARD requirement, not a degradation: "
                  "unsanitised rich text stored once is stored forever, so a "
                  "build without the app must refuse to start rather than save "
                  "whatever the browser sent. Set BUILD_ANTIVIRUS=1."),
            id="cyprian.E003",
        ))
    if not apps.is_installed("toto.verbena"):
        errors.append(Error(
            "toto.cyprian requires toto.verbena.",
            hint=(
                "Cyprian serialises verbena's page/section structure and reuses "
                "its slug and form helpers. verbena is in BASE_APPS, so this "
                "normally cannot happen."
            ),
            id="cyprian.E002",
        ))

    return errors
