import os
import sys
from io import StringIO
from django.conf import settings
from django.apps import apps
from django.core.management.base import BaseCommand
from django.core.management import call_command


class Command(BaseCommand):
    help = "Run ingress commands for all apps listed in settings.INGRESS_ALLOWED_APPS"

    class IngressCommandError(Exception):
        """Base class for ingress command errors."""

    class IngressCommandNotFound(IngressCommandError):
        """Raised when the ingress command file is missing."""

    class IngressCommandExecutionFailed(IngressCommandError):
        """Raised when the command execution throws an error."""

    def run_ingress_for_app(self, app_name):
        """Run the ingress command for a single app."""
        app_config = apps.get_app_config(app_name)
        cmd_path = os.path.join(
            app_config.path, "management", "commands", f"ingress_{app_name}.py"
        )

        if not os.path.isfile(cmd_path):
            raise self.IngressCommandNotFound(f"No ingress command found for '{app_name}'")

        try:
            out = StringIO()
            full_ingress_mode = getattr(settings, "FULL_INGRESS", False)
            call_command(f"ingress_{app_name}", stdout=out, stderr=out, full=full_ingress_mode)
            output = out.getvalue()
            sys.stdout.write(output)
        except Exception as e:
            raise self.IngressCommandExecutionFailed(
                f"Error running ingress for '{app_name}': {str(e)}"
            )

    def handle(self, *args, **options):
        allowed_apps = getattr(settings, "INGRESS_ALLOWED_APPS", [])
        total = len(allowed_apps)
        success = 0
        not_found = 0
        failed = 0

        for app_name in allowed_apps:
            try:
                self.run_ingress_for_app(app_name)
                self.stdout.write(self.style.SUCCESS(f"✅ Success: {app_name}"))
                success += 1
            except self.IngressCommandNotFound as nf:
                self.stdout.write(self.style.WARNING(f"⚠️ Not Found: {nf}"))
                not_found += 1
            except self.IngressCommandExecutionFailed as ef:
                self.stdout.write(self.style.ERROR(f"💥 Failed: {ef}"))
                failed += 1
            except self.IngressCommandError as e:
                self.stdout.write(self.style.NOTICE(f"❓ Unknown Error: {e}"))
                failed += 1

        self.stdout.write("Summary:")
        self.stdout.write(f"Total: {total}")
        self.stdout.write(self.style.SUCCESS(f"✅ Success: {success}"))
        self.stdout.write(self.style.WARNING(f"⚠️ Not Found: {not_found}"))
        self.stdout.write(self.style.ERROR(f"💥 Failed: {failed}"))
