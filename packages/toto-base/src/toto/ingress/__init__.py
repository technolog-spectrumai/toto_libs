import json
import os

from django.conf import settings
from django.core.management.base import BaseCommand

from toto.conf import data_dir


class IngressCommand(BaseCommand):
    help = "Base command that optionally accepts a JSON string"

    DATA_ROOT = str(data_dir(os.path.join(settings.BASE_DIR, "../..", "data")))

    @staticmethod
    def read_text(*parts):
        path = os.path.join(IngressCommand.DATA_ROOT, *parts)
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def read_json(*parts):
        path = os.path.join(IngressCommand.DATA_ROOT, *parts)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def __init__(self):
        super().__init__()
        self.full = False
        self.rich = False

    def add_arguments(self, parser):
        parser.add_argument(
            "--full",
            action="store_true",
            help="If set, run full data fill; otherwise, only process indispensable data",
        )
        # Rich vs. thin ingress. Default comes from settings.RAVIOLI_RICH_INGRESS;
        # --rich / --thin force it for a single run.
        parser.add_argument(
            "--rich",
            dest="rich",
            action="store_true",
            default=None,
            help="Force rich ingress (seed sample data into Neo4j — slow).",
        )
        parser.add_argument(
            "--thin",
            dest="rich",
            action="store_false",
            help="Force thin ingress (skip heavy Neo4j seeding — fast).",
        )

    def handle(self, *args, **options):
        self.full = options.get("full", False)
        rich_opt = options.get("rich", None)
        self.rich = (
            getattr(settings, "RAVIOLI_RICH_INGRESS", False)
            if rich_opt is None
            else rich_opt
        )
        self.bootstrap()
        self.process()

    #: Set False on a command that must not touch the economy — a dry run, or
    #: one that deliberately tests a bare install.
    bootstrap_economy = True

    def bootstrap(self):
        """What EVERY ingress path guarantees, before its own work.

        The currencies a platform is not itself without have to exist before
        anything can be priced, billed, rewarded or paid out — and they were
        seeded by exactly one command, so an operator who ran any of the other
        thirty-odd got a working app on an economy that did not exist. That is
        not a hypothetical: with no currencies the Assets UI answers 500 on
        every mint and no tariff price can be saved at all.

        Cheap when there is nothing to do, idempotent when there is, and never
        fatal: `ingress_forum` must not fail because the economy could not be
        set up. See toto.assets.services.bootstrap.
        """
        if not self.bootstrap_economy:
            return
        from django.apps import apps as django_apps

        if not django_apps.is_installed("toto.assets"):
            return
        try:
            from toto.assets.services.bootstrap import bootstrap_economy
        except ImportError:
            return                      # a host that ships no economy at all
        try:
            bootstrap_economy(reporter=self.stdout)
        except Exception as exc:        # noqa: BLE001
            self.stderr.write(
                f"  ⚠ economy bootstrap skipped: {exc}")

    def process(self):
        raise NotImplementedError("Subclasses must implement process()")
