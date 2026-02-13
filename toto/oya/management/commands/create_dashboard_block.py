from django.core.management.base import BaseCommand
from oya.models import DashboardBlock


class Command(BaseCommand):
    help = "Create a new DashboardBlock entry"

    def add_arguments(self, parser):
        parser.add_argument("title", type=str, help="Title of the dashboard block")
        parser.add_argument("--description", type=str, default="", help="Optional description of the dashboard block")
        parser.add_argument("--icon", type=str, default="", help="Optional Font Awesome icon class")
        parser.add_argument("--link", type=str, default="", help="Optional link path")

    def handle(self, *args, **kwargs):
        title = kwargs["title"]
        description = kwargs.get("description", "")
        icon = kwargs.get("icon", "")
        link = kwargs.get("link", "")

        block = DashboardBlock.objects.create(title=title, description=description, icon=icon, link=link)
        self.stdout.write(self.style.SUCCESS(f"Successfully created dashboard block: {block.title}"))
