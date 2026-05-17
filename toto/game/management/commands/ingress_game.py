from django.core.management import call_command

from toto.ingress import IngressCommand


class Command(IngressCommand):
    help = "Ingress Toto Game economy definitions and optional demo data."

    def process(self):
        args = []
        if self.full:
            args.append("--first-user-demo")
        call_command("seed_economy", *args, stdout=self.stdout, stderr=self.stderr)
