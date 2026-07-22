from django.core.management.base import BaseCommand

from toto.mandragora.kernel_server import KernelServer


class Command(BaseCommand):
    help = "Run the Jupyter kernel ZMQ server."

    def add_arguments(self, parser):
        parser.add_argument(
            "--bind-addr",
            default="tcp://0.0.0.0:5555",
            help="ZeroMQ bind address (default: tcp://0.0.0.0:5555).",
        )

    def handle(self, *args, **options):
        KernelServer(bind_addr=options["bind_addr"]).run()
