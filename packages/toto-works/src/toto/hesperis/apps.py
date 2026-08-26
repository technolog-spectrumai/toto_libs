from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured


class HesperisConfig(AppConfig):
    """Crowdsourced collection, verification and versioned datasets.

    A SURFACE over toto.kanban, not a second work system. Campaigns, missions,
    tasks, submissions, reviews, consensus and rewards all belong to the
    engine; this app adds bounty semantics and the durable dataset side.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.hesperis"
    verbose_name = "Hesperis"

    def ready(self):
        from django.apps import apps

        # Hesperis carries real foreign keys into assets.Asset and
        # assets.LedgerAccount — gems are ledger holdings, and the whole point
        # of reusing toto.assets is that there is no second balance system.
        # Without that app the FKs are fields.E300 and every command fails with
        # a message about an abstract model, which reads like a broken app
        # rather than a missing one. Say what is actually wrong instead.
        for required in ("toto.kanban", "toto.assets"):
            if not apps.is_installed(required):
                raise ImproperlyConfigured(
                    f"toto.hesperis requires {required} in INSTALLED_APPS: it "
                    "is a surface over the kanban work engine and settles its "
                    "rewards on the assets ledger."
                )
