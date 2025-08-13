from django.core.management.base import BaseCommand
from gervazy.models import TLSCertificate
import os


class Command(BaseCommand):
    help = "Save an existing TLS certificate to a specified directory"

    def add_arguments(self, parser):
        parser.add_argument('common_name', type=str, help="Common Name (CN) of the certificate to save")
        parser.add_argument('output_dir', type=str, help="Directory to save the certificate files")

    def handle(self, *args, **options):
        try:
            # Retrieve the certificate from the database
            cert = TLSCertificate.objects.get(common_name=options['common_name'])

            # Ensure the output directory exists
            os.makedirs(options['output_dir'], exist_ok=True)

            # File paths
            cert_file_path = os.path.join(options['output_dir'], f"{cert.common_name}.crt")
            key_file_path = os.path.join(options['output_dir'], f"{cert.common_name}.key")

            # Save certificate and private key
            with open(cert_file_path, "w") as cert_file:
                cert_file.write(cert.certificate)

            with open(key_file_path, "w") as key_file:
                key_file.write(cert.private_key)

            self.stdout.write(self.style.SUCCESS(
                f"Certificate files saved for {cert.common_name} in {options['output_dir']}"
            ))

        except TLSCertificate.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"Error: No certificate found for Common Name '{options['common_name']}'"))

        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Error saving certificate: {e}"))
