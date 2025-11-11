from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command


class Command(BaseCommand):
    help = "Run debug init: reset platform, then ingress + sync for each hardcoded app"

    # Hardcoded list of apps to process
    APPS = ["federal", "community", "finance", "portfolio", "events", "ravioli"]

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Starting debug init sequence..."))

        # Step 1: init_platform --reset
        self.stdout.write(self.style.NOTICE("Running init_platform --reset"))
        try:
            call_command("init_platform", "--reset")
        except CommandError as e:
            self.stderr.write(self.style.ERROR(f"init_platform failed: {e}"))
            return

        # Step 2: loop over hardcoded apps
        for app in self.APPS:
            self.stdout.write(self.style.NOTICE(f"Processing app: {app}"))

            # ingress_{app} --full
            ingress_cmd = f"ingress_{app}"
            self.stdout.write(self.style.NOTICE(f"Running {ingress_cmd} --full"))
            try:
                call_command(ingress_cmd, "--full")
            except CommandError as e:
                self.stderr.write(self.style.ERROR(f"{ingress_cmd} failed: {e}"))
                continue

            # sync_{app}
            sync_cmd = f"sync_{app}"
            self.stdout.write(self.style.NOTICE(f"Running {sync_cmd}"))
            try:
                call_command(sync_cmd)
            except CommandError as e:
                self.stderr.write(self.style.ERROR(f"{sync_cmd} failed: {e}"))
                continue

        self.stdout.write(self.style.SUCCESS("Debug init sequence completed successfully."))
