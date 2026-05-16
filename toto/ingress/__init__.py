import json
import os

from django.conf import settings
from django.core.management.base import BaseCommand


class IngressCommand(BaseCommand):
    help = "Base command that optionally accepts a JSON string"

    DATA_ROOT = os.path.join(settings.BASE_DIR, "../..", "data")

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

    def add_arguments(self, parser):
        parser.add_argument(
            "--full",
            action="store_true",
            help="If set, run full data fill; otherwise, only process indispensable data",
        )

    def handle(self, *args, **options):
        self.full = options.get("full", False)
        self.process()

    def process(self):
        raise NotImplementedError("Subclasses must implement process()")
