import os
import sys
from io import StringIO
from django.conf import settings
from django.apps import apps
from django.core.management.base import BaseCommand
from django.core.management import call_command


class Command(BaseCommand):
    help = "Run graph setup commands for all apps listed in settings.GRAPH_ALLOWED_APPS"

    class GraphCommandError(Exception):
        """Base class for graph command errors."""

    class GraphCommandNotFound(GraphCommandError):
        """Raised when the graph command file is missing."""

    class GraphCommandExecutionFailed(GraphCommandError):
        """Raised when the graph command execution throws an error."""

    def run_graph_for_app(self, app_name):
        """Run the graph command for a single app."""
        app_config = apps.get_app_config(app_name)
        cmd_path = os.path.join(
            app_config.path, "management", "commands", f"graph_{app_name}.py"
        )

        if not os.path.isfile(cmd_path):
            raise self.GraphCommandNotFound(f"No graph command found for '{app_name}'")

        try:
            out = StringIO()
            call_command(f"graph_{app_name}", stdout=out, stderr=out)
            output = out.getvalue()
            sys.stdout.write(output)
        except Exception as e:
            raise self.GraphCommandExecutionFailed(
                f"Error running graph for '{app_name}': {str(e)}"
            )

    def handle(self, *args, **options):
        allowed_apps = getattr(settings, "GRAPH_ALLOWED_APPS", [])
        total = len(allowed_apps)
        success = 0
        not_found = 0
        failed = 0

        for app_name in allowed_apps:
            try:
                self.run_graph_for_app(app_name)
                self.stdout.write(self.style.SUCCESS(f"✅ Success: {app_name}"))
                success += 1
            except self.GraphCommandNotFound as nf:
                self.stdout.write(self.style.WARNING(f"⚠️ Not Found: {nf}"))
                not_found += 1
            except self.GraphCommandExecutionFailed as ef:
                self.stdout.write(self.style.ERROR(f"💥 Failed: {ef}"))
                failed += 1
            except self.GraphCommandError as e:
                self.stdout.write(self.style.NOTICE(f"❓ Unknown Error: {e}"))
                failed += 1

        self.stdout.write("Summary:")
        self.stdout.write(f"Total: {total}")
        self.stdout.write(self.style.SUCCESS(f"✅ Success: {success}"))
        self.stdout.write(self.style.WARNING(f"⚠️ Not Found: {not_found}"))
        self.stdout.write(self.style.ERROR(f"💥 Failed: {failed}"))
