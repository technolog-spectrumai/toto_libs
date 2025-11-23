import json
from django.core.management.base import BaseCommand, CommandError
from oya.models import DashboardBlock


class IngressCommand(BaseCommand):
    help = "Base command that optionally accepts a JSON string"

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
            link=item_data.get('link'),
            public=item_data.get('public', True)
        )
        self.stdout.write(f"Created DashboardBlock: {block.title}")
        return block
