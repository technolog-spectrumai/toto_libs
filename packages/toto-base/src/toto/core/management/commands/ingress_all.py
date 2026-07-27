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

    def run_ingress_for_app(self, app_name):
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
            full_ingress_mode = getattr(settings, "FULL_INGRESS", False)
            call_command(f"ingress_{label}", stdout=out, stderr=out, full=full_ingress_mode)
            sys.stdout.write(out.getvalue())
        except Exception as e:
            raise self.IngressCommandExecutionFailed(
                f"Error running ingress for '{label}': {str(e)}"
            )

    def handle(self, *args, **options):
        allowed_apps = getattr(settings, "INGRESS_ALLOWED_APPS", [])
        total = len(allowed_apps)
        success = 0
        not_found_list = []
        failed_list = []

        for app_name in allowed_apps:
            try:
                self.run_ingress_for_app(app_name)
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
