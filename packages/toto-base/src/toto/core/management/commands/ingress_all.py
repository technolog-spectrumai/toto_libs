import os
import sys
from io import StringIO
from django.conf import settings
from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command


from toto.features import INGRESS_MODES, INGRESS_NONE, INGRESS_FULL, ingress_mode


class Command(BaseCommand):
    """Run ``ingress_<label>`` for every app in ``settings.INGRESS_ALLOWED_APPS``.

    The mode (``toto.features.INGRESS_MODES``) comes from ``--mode``, else
    ``settings.INGRESS_MODE``, else the older ``FULL_INGRESS``. ``none`` runs
    nothing at all — not even the economy bootstrap the commands share.
    ``--strict`` exits non-zero when any command failed, which is what the
    container entrypoint wants for the compulsory (realistic) set: a platform
    that booted without its compulsory rows should be noticed, not served.
    """
    help = "Run ingress commands for all apps listed in settings.INGRESS_ALLOWED_APPS"

    def add_arguments(self, parser):
        parser.add_argument("--mode", choices=INGRESS_MODES, default=None,
                            help="none | realistic | full (default: the host settings)")
        parser.add_argument("--strict", action="store_true",
                            help="exit 1 when any ingress command failed")

    class IngressCommandError(Exception):
        pass

    class IngressCommandNotFound(IngressCommandError):
        pass

    class IngressCommandExecutionFailed(IngressCommandError):
        pass

    @staticmethod
    def resolve_app_label(app_name: str) -> str:
        """
        Convert dotted path to Django app label.
        Example:
            'toto.core' -> 'core'
        """
        return app_name.split(".")[-1]

    def run_ingress_for_app(self, app_name, mode):
        label = self.resolve_app_label(app_name)

        # Try to load the app config
        try:
            app_config = apps.get_app_config(label)
        except LookupError:
            raise self.IngressCommandExecutionFailed(
                f"No installed app with label '{label}'"
            )

        # Path to ingress command file
        cmd_path = os.path.join(
            app_config.path, "management", "commands", f"ingress_{label}.py"
        )

        if not os.path.isfile(cmd_path):
            raise self.IngressCommandNotFound(
                f"No ingress command found for '{label}'"
            )

        try:
            out = StringIO()
            call_command(f"ingress_{label}", stdout=out, stderr=out, mode=mode)
            sys.stdout.write(out.getvalue())
        except Exception as e:
            raise self.IngressCommandExecutionFailed(
                f"Error running ingress for '{label}': {str(e)}"
            )

    @staticmethod
    def configured_mode() -> str:
        configured = getattr(settings, "INGRESS_MODE", None)
        if configured in INGRESS_MODES:
            return configured
        return ingress_mode(lambda name: getattr(settings, name, None))

    def handle(self, *args, **options):
        mode = options.get("mode") or self.configured_mode()
        self.stdout.write(f"Ingress mode: {mode}")
        if mode == INGRESS_NONE:
            self.stdout.write("Nothing to seed: ingress mode none.")
            return
        allowed_apps = getattr(settings, "INGRESS_ALLOWED_APPS", [])
        total = len(allowed_apps)
        success = 0
        not_found_list = []
        failed_list = []

        for app_name in allowed_apps:
            try:
                self.run_ingress_for_app(app_name, mode)
                self.stdout.write(self.style.SUCCESS(f"✅ Success: {app_name}"))
                success += 1

            except self.IngressCommandNotFound as nf:
                self.stdout.write(self.style.WARNING(f"⚠️ Not Found: {nf}"))
                not_found_list.append(str(nf))

            except self.IngressCommandExecutionFailed as ef:
                self.stdout.write(self.style.ERROR(f"💥 Failed: {ef}"))
                failed_list.append(str(ef))

            except self.IngressCommandError as e:
                self.stdout.write(self.style.NOTICE(f"❓ Unknown Error: {e}"))
                failed_list.append(str(e))

        self.stdout.write("\nSummary:")
        self.stdout.write(f"Total: {total}")
        self.stdout.write(self.style.SUCCESS(f"✅ Success: {success}"))
        self.stdout.write(self.style.WARNING(f"⚠️ Not Found: {len(not_found_list)}"))
        self.stdout.write(self.style.ERROR(f"💥 Failed: {len(failed_list)}"))

        if not_found_list:
            self.stdout.write(self.style.WARNING("\n--- Not Found ---"))
            for msg in not_found_list:
                self.stdout.write(self.style.WARNING(f"  ⚠️  {msg}"))

        if failed_list:
            self.stdout.write(self.style.ERROR("\n--- Errors ---"))
            for msg in failed_list:
                self.stdout.write(self.style.ERROR(f"  💥  {msg}"))
            if options.get("strict"):
                raise CommandError(
                    f"{len(failed_list)} ingress command(s) failed in mode {mode}")
