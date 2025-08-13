from django.core.management.base import BaseCommand
from gervazy.models import TLSCertificate  # Import Address for lookup
from oya.models import Address


class Command(BaseCommand):
    help = "Generate and store a new TLS certificate based on an existing address"

    def add_arguments(self, parser):
        parser.add_argument('common_name', type=str, help='Common Name (CN)')
        parser.add_argument('organization_name', type=str, help='Organization Name (O)')
        parser.add_argument('address_id', type=int, help='Address ID to use for certificate details')

    def handle(self, *args, **options):
        try:
            # Fetch address details using the provided ID
            address = Address.objects.get(id=options['address_id'])

            # Create TLS Certificate using address data
            cert = TLSCertificate.objects.create(
                common_name=options['common_name'],
                organization_name=options['organization_name'],
                country_name=address.country_name,
                state_or_province_name=address.state_or_province_name,
                locality_name=address.locality_name,
            )

            self.stdout.write(self.style.SUCCESS(f"TLS certificate created for {cert.common_name}"))

        except Address.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"Error: Address with ID {options['address_id']} does not exist."))

        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Error generating certificate: {e}"))
