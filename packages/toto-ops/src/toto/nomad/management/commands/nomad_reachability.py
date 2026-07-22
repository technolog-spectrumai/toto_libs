from django.core.management.base import BaseCommand, CommandError

from toto.nomad import service


def _onoff(value: str) -> bool:
    v = value.strip().lower()
    if v in ("on", "true", "1", "yes"):
        return True
    if v in ("off", "false", "0", "no"):
        return False
    raise CommandError(f"expected on/off, got {value!r}")


class Command(BaseCommand):
    help = (
        "Show or set faros transport reachability. The escape hatch when a superuser "
        "disables the transport they were connected over.\n"
        "  manage.py nomad_reachability                 # print current state\n"
        "  manage.py nomad_reachability --clearnet on   # re-enable clearnet\n"
        "  manage.py nomad_reachability --onion off     # unpublish the onion"
    )

    def add_arguments(self, parser):
        parser.add_argument("--onion", help="on|off — publish/unpublish the .onion")
        parser.add_argument("--clearnet", help="on|off — serve/refuse clearnet traffic")

    def handle(self, *args, **options):
        if options.get("onion") is not None:
            service.set_onion_enabled(_onoff(options["onion"]))
        if options.get("clearnet") is not None:
            service.set_clearnet_enabled(_onoff(options["clearnet"]))

        r = service.reachability()
        self.stdout.write(
            f"onion={'on' if r['onion_enabled'] else 'off'} "
            f"clearnet={'on' if r['clearnet_enabled'] else 'off'}"
        )
