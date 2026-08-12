from django.apps import AppConfig

from toto.core.plugin_autodiscover import autodiscover_plugins


class RepoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.repo"

    def ready(self):
        from . import signals  # noqa: F401
        # Workflow-engine task (workflows' ready() also autodiscovers this;
        # the explicit import matches fileservices/texlab and is harmless).
        try:
            from . import predefined_tasks  # noqa: F401
        except Exception:
            pass
        # Remote credential providers: every installed app's `remotes` module,
        # discovered rather than imported by name, so this app never mentions
        # toto.gitea. Finds nothing on a host that installs only the local half,
        # which is the intended and supported state — see remotes.py.
        autodiscover_plugins("remotes")
