from django.apps import AppConfig


class StevenConfig(AppConfig):
    """Steven: the assistant that edits what you have selected.

    A rewrite, not a revival. The 235-line shell this replaces was a floating
    WebSocket chat widget with no models; the 4,168-line app in
    `toto_libs/limbo/old_steven` that preceded THAT is still parked, and stays
    parked — its credential path was a no-op, its RAG read fields that had been
    deleted, and it carried LangChain, spaCy and an `eval()`.

    What survives from it is the idea: meter inference, and queue it rather than
    holding a web worker.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.steven"
    verbose_name = "Steven"

    def ready(self):
        # Pure data, no models, no database: ready() runs before migrate and
        # during collectstatic.
        from toto.core.plugin_autodiscover import autodiscover_plugins

        from . import metrics, surfaces, sweeps  # noqa: F401

        # Every editor's ai_surfaces.py — the whole extension point. There is no
        # floating widget any more: the old shell's whole content was one, and
        # four of the six editors blank the block it renders into, so a global
        # overlay was never going to be where this feature lived.
        autodiscover_plugins("ai_surfaces")
