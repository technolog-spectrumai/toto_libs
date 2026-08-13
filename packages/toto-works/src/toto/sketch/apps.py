from django.apps import AppConfig
from django.core.checks import Error, register


class SketchConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.sketch"
    verbose_name = "Sketch Drawings"

    def ready(self):
        register(_antivirus_is_required)


def _antivirus_is_required(app_configs, **kwargs):
    """Sketch will not run on a host without a scanner. Deliberately.

    Every other consumer of ``toto.vault.scanning`` degrades quietly when
    antivirus is absent, because for them screening is an improvement on what
    they did before. Sketch is the opposite case: it carried its own
    detect-and-refuse guard, that guard was deleted in favour of the shared
    one, and this editor **renders SVG inline, in our origin**. Degrading
    quietly here would mean silently un-guarding an editor that used to be
    guarded — the failure mode being an executable drawing displayed in the
    page, with no error anywhere to say the check stopped happening.

    So the coupling is loud and it is checked: a build with BUILD_SKETCH and
    without BUILD_ANTIVIRUS fails ``manage.py check`` and never starts.
    """
    from django.apps import apps

    if apps.is_installed("toto.antivirus"):
        return []
    return [Error(
        "toto.sketch requires toto.antivirus, which is not installed.",
        hint="Set BUILD_ANTIVIRUS=1 on any host that sets BUILD_SKETCH=1. "
             "Sketch renders SVG inline and has no screening of its own.",
        id="sketch.E001",
    )]
