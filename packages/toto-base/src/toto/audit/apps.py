from django.apps import AppConfig


class AuditConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.audit"
    # The label stays `audit`, which is what keeps `audit_auditrecord` and the
    # `audit` rows in django_migrations valid on a host that carried this app
    # before it moved into the suite. Renaming it would orphan a live chain.
    label = "audit"
    verbose_name = "Audit trail"

    def ready(self):
        # Sign-ins, sign-outs and accounts (2026-09-28): identity.py.
        from . import identity

        identity.connect()
