from django.core.management.base import BaseCommand
from community.models import Community, Address


class Command(BaseCommand):
    help = "Create a Community with an associated Address"

    def add_arguments(self, parser):
        parser.add_argument('name', type=str, help='Name of the community')
        parser.add_argument('address_id', type=int, help='ID of the associated address')
        parser.add_argument('--established_year', type=int, help='Year the community was established (optional)')

    def handle(self, *args, **options):
        name = options['name']
        address_id = options['address_id']
        established_year = options.get('established_year', None)

        # Retrieve Address
        try:
            address = Address.objects.get(id=address_id)
        except Address.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"Address with ID {address_id} does not exist."))
            return

        # Create Community
        community, created = Community.objects.get_or_create(
            name=name,
            defaults={
                "address": address,
                "established_year": established_year
            }
        )

        if created:
            self.stdout.write(self.style.SUCCESS(f"Successfully created Community: {community.name}"))
        else:
            self.stdout.write(self.style.WARNING(f"Community already exists: {community.name}"))
