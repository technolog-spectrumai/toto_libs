from django.apps import AppConfig


class MailConfig(AppConfig):
    """The mailbox app.

    Distinct from :mod:`toto.jess`, deliberately: jess is the platform's
    TRANSPORT and its operator console — one shared account, every page
    staff-403. This app is the product a person uses: their own external
    mailbox, connected with their own credentials, plus the one governed
    system mailbox the platform speaks with.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.mail"
    label = "mail"
    verbose_name = "Mail"

    def ready(self):
        from . import metrics  # noqa: F401 — registers the mail.send metric
