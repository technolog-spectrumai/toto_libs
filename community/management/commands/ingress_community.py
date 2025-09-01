from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command


class Command(BaseCommand):
    help = "Initialize platform by clearing the database, running migrations, generating TLS certificate, creating an address, setting up config, and creating a superuser"

    def handle(self, *args, **options):

        self.stdout.write(self.style.NOTICE("Creating company address..."))
        address_args = self.get_address_arguments()
        call_command("create_address", **address_args)
        self.stdout.write(self.style.SUCCESS("Company address created."))

        latest_address_id = self.get_latest_address_id()
        if latest_address_id is None:
            self.stderr.write(self.style.ERROR("No address found. Initialization aborted."))
            return

        company_name = "Our Thing Inc."
        self.stdout.write(self.style.NOTICE("Creating company..."))
        call_command("create_company", company_name, str(latest_address_id), "--established_year=2024")
        self.stdout.write(self.style.SUCCESS("Company created."))

        latest_company_id = self.get_latest_company_id()
        if latest_company_id is None:
            self.stderr.write(self.style.ERROR("No company found. Initialization aborted."))
            return

    def get_address_arguments(self):
        """Returns a dictionary of address arguments"""
        return {
            "country_name": "US",
            "state_or_province_name": "California",
            "locality_name": "San Francisco",
            "street": "123 Business St",
            "building": "HQ Tower",
            "apartment": "5A"
        }

    def get_latest_address_id(self):
        """Fetches the latest Address ID to be used in platform creation"""
        from community.models import Address  # Import inside method to avoid potential issues
        latest_address = Address.objects.order_by("-id").first()
        return latest_address.id if latest_address else None

    def get_latest_company_id(self):
        """Fetches the latest Company ID to be used in platform creation"""
        from community.models import Company  # Import locally to avoid circular imports
        latest_company = Company.objects.order_by("-id").first()
        return latest_company.id if latest_company else None

