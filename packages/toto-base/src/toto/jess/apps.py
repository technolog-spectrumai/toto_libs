from django.apps import AppConfig


class JessConfig(AppConfig):
    """The platform's post — see README.md for the cat this is named after.

    ``ready`` connects one signal and nothing else. Two things it might have
    been tempted to do remain wrong here:

    * asserting that toto.gervazy is installed — gervazy is a CORE_APP
      (registry.py:13), part of the irreducible core/gervazy/people/locations/events
      cycle, so a host cannot have jess without it;
    * touching the database or opening the vault — ready() runs before migrations
      and during collectstatic, so anything that queries fails on a fresh tree.

    The signal hookup does neither: it registers a receiver that drops a
    process-memory credential (``credentials.py``) when the session that typed
    it logs out. Import-time only, no queries, no vault.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.jess"
    label = "jess"
    verbose_name = "Jess (mail)"

    def ready(self):
        from django.contrib.auth.signals import user_logged_out

        from . import credentials

        user_logged_out.connect(
            credentials.on_user_logged_out,
            dispatch_uid="jess.credentials.on_user_logged_out",
        )
