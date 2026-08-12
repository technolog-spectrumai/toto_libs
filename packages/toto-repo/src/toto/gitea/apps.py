from django.apps import AppConfig


class GiteaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.gitea"
    verbose_name = "Gitea"

    # No ready() hook on purpose. The one thing this app offers another app —
    # credentials for its own remotes — lives in `remotes`, which toto.repo
    # DISCOVERS. Registering it from here would run on a host that has no
    # toto.repo to register with, and would make the direction of the coupling
    # a matter of import order rather than of design.
