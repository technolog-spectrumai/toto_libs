import json
from django.core.management.base import BaseCommand, CommandError


class IngressCommand(BaseCommand):
    help = "Base command that optionally accepts a JSON string"

    def add_arguments(self, parser):
        parser.add_argument(
            '--json',
            type=str,
            required=False,
            help="Optional JSON string to be processed by the command"
        )

    def handle(self, *args, **options):
        json_input = options.get('json', '')

        if not json_input:
            return
        try:
            data = json.loads(json_input)
        except json.JSONDecodeError as e:
            raise CommandError(f"Invalid JSON input: {e}")

        self.process(data)

    def process(self, data):
        raise NotImplementedError("Subclasses must implement process_json method")
