from toto.ingress import IngressCommand


class Command(IngressCommand):
    help = "Verbena is a utility app; concrete seed data lives in apps that use it."

    def process(self):
        self.stdout.write(self.style.WARNING("Verbena has no concrete ingress data."))
