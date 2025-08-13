from django.core.management.base import BaseCommand
from oya.models import Address


class Command(BaseCommand):
    help = "Create a new Address instance for a company"

    def add_arguments(self, parser):
        parser.add_argument('--country_name', type=str, default="US", help='Country')
        parser.add_argument('--state_or_province_name', type=str, default="California", help='State/Province')
        parser.add_argument('--locality_name', type=str, default="San Francisco", help='City or locality')
        parser.add_argument('--street', type=str, default="123 Business St", help='Street name')
        parser.add_argument('--building', type=str, default="HQ Tower", help='Building number')
        parser.add_argument('--apartment', type=str, default=None, help='Apartment number (optional)')

    def handle(self, *args, **options):
        """Creates a new Address instance based on command-line arguments"""
        self.stdout.write(self.style.NOTICE("Creating company address..."))
        address, created = Address.objects.update_or_create(
            country_name=options['country_name'],
            state_or_province_name=options['state_or_province_name'],
            locality_name=options['locality_name'],
            street=options['street'],
            building=options['building'],
            defaults={'apartment': options['apartment']}  # Apartment is optional
        )

        if created:
            self.stdout.write(self.style.SUCCESS(f"Successfully created Address: {str(address.id)}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Updated existing Address: {str(address.id)}"))
