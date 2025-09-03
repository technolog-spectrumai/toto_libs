import json
from django.core.management.base import BaseCommand, CommandError
from oya.models import DashboardBlock


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
        json_input = options.get('json', '{}')

        if not json_input:
            self.stdout.write("No JSON input provided.")
            data = {}
        else:
            try:
                data = json.loads(json_input)
            except json.JSONDecodeError as e:
                raise CommandError(f"Invalid JSON input: {e}")
        self.process(data)

    def process(self, data):
        raise NotImplementedError("Subclasses must implement process(data)")

    def create_dashboard_item(self, **item_data):
        """
        Creates a DashboardBlock instance from a dictionary.
        Expected keys: title, description, icon, link
        """
        required_fields = ['title', 'description', 'icon']
        missing = [field for field in required_fields if field not in item_data]
        if missing:
            raise CommandError(f"Missing required fields for DashboardBlock: {', '.join(missing)}")

        block = DashboardBlock.objects.create(
            title=item_data['title'],
            description=item_data['description'],
            icon=item_data['icon'],
            link=item_data.get('link')  # Optional
        )
        self.stdout.write(f"Created DashboardBlock: {block.title}")
        return block
