from django.apps import AppConfig
from django.core.checks import Error, register


@register()
def _workflows_present(app_configs, **kwargs):
    """antivirus.E001 — the scanner's job runner must exist.

    On-demand scans queue through toto.workflows (with an inline fallback for
    a worker that is DOWN — not for machinery that is ABSENT). Workflows is
    compulsory platform-wide since 8/2026 and toto.features enforces that for
    every flag-driven host; this check is the belt for hand-rolled settings,
    so a build that quietly dropped the app fails `manage.py check` instead of
    failing at the first Scan click.
    """
    from django.apps import apps

    if apps.is_installed("toto.workflows"):
        return []
    return [Error(
        "toto.antivirus requires toto.workflows, which is not installed.",
        hint="Workflows is compulsory since 8/2026 (toto.features forces it). "
             "A settings file that installs antivirus without workflows is "
             "hand-broken, not configured.",
        id="antivirus.E001",
    )]


class AntivirusConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.antivirus"
    verbose_name = "Antivirus"

    def ready(self):
        # Scanners other apps declare in <app>/scanners.py. Discovery runs from
        # HERE rather than each owner's ready(), so an app can ship a scanner to
        # a host with no antivirus and pay nothing for it — the module is simply
        # never imported there. Same reasoning as cyprian's bridges.
        from toto.core.plugin_autodiscover import autodiscover_plugins

        from . import scanners  # noqa: F401  - registers the built-ins

        autodiscover_plugins("scanners")
