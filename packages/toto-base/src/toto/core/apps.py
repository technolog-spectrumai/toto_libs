from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'toto.core'
    url_name = "toto.core"

    def ready(self):
        from django.core.exceptions import ImproperlyConfigured
        from django.db.backends.signals import connection_created

        from toto.versioning import TotoVersionError, check_runtime_coherence

        # toto.core is installed by every host, so this is the one place that
        # sees the whole suite. Refuse to boot a half-upgraded installation
        # rather than fail later in some unrelated import.
        try:
            check_runtime_coherence()
        except TotoVersionError as exc:
            raise ImproperlyConfigured(str(exc)) from exc

        def _set_sqlite_wal(sender, connection, **kwargs):
            if connection.vendor == "sqlite":
                connection.cursor().execute("PRAGMA journal_mode=WAL;")
                connection.cursor().execute("PRAGMA synchronous=NORMAL;")

        # weak=False: a local function is collected the moment this method
        # returns, and only DEBUG=True's argument-check cache kept it alive —
        # every deployed profile ran SQLite without WAL.
        connection_created.connect(_set_sqlite_wal, weak=False,
                                   dispatch_uid="core_sqlite_wal")

        # The sign-in lockout (2026-09-30): counting failures and clearing a
        # pair on success. Enforcement is its backend, in AUTHENTICATION_BACKENDS.
        from toto.core import signin_lockout

        signin_lockout.connect()
        # A row per sign-in, so a member's sessions can be listed and ended
        # (My account, 2026-09-30).
        from toto.core import user_sessions

        user_sessions.connect()
        # The system checks (2026-10-01): REQUIRE_SMTP's, which stops a
        # process whose mail settings could not send, where a host asks.
        from toto.core import checks  # noqa: F401

        self._admin_says_why()

    @staticmethod
    def _admin_says_why():
        """The admin login names a lockout refusal instead of calling it a
        wrong password — unless the host set a login form of its own."""
        from django.apps import apps

        if not apps.is_installed("django.contrib.admin"):
            return
        from django.contrib import admin

        from toto.core.admin_login import SigninLockoutAdminAuthenticationForm

        if admin.site.login_form is None:
            admin.site.login_form = SigninLockoutAdminAuthenticationForm

