import json
from django.core.management.base import BaseCommand, CommandError
import os
from django.conf import settings


class IngressCommand(BaseCommand):
    help = "Base command that optionally accepts a JSON string"

    # 📂 Data root: parent of project root
    DATA_ROOT = os.path.join(settings.BASE_DIR, "../..", "data")

    @staticmethod
    def read_text(*parts):
        """
        Read a UTF-8 text file from the data directory.
        Usage: IngressCommand.read_text("subdir", "file.txt")
        """
        path = os.path.join(IngressCommand.DATA_ROOT, *parts)
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def read_json(*parts):
        """
        Read a JSON file from the data directory.
        Usage: IngressCommand.read_json("subdir", "file.json")
        """
        path = os.path.join(IngressCommand.DATA_ROOT, *parts)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def __init__(self):
        super().__init__()
        self.full = False

    def add_arguments(self, parser):
        parser.add_argument(
            '--full',
            action='store_true',
            help="If set, run full data fill; otherwise, only process indispensable data"
        )

    def handle(self, *args, **options):
        self.full = options.get('full', False)
        self.process()

    def process(self):
        raise NotImplementedError("Subclasses must implement process()")
