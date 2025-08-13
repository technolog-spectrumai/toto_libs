from django.db import models
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from datetime import datetime, timedelta
from io import BytesIO
from django.core.files.base import ContentFile
import qrcode
import os


class BaseCertificate(models.Model):
    common_name = models.CharField(max_length=255, verbose_name="Common Name (CN)")
    issued_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    def __str__(self):
        return f"Certificate for {self.common_name}"


class TLSCertificate(BaseCertificate):
    country_name = models.CharField(max_length=2)
    state_or_province_name = models.CharField(max_length=100)
    locality_name = models.CharField(max_length=100)
    organization_name = models.CharField(max_length=255)
    private_key = models.TextField(blank=True, null=True)
    certificate = models.TextField(blank=True, null=True)
    qr_code = models.ImageField(upload_to="qr_codes/", blank=True, null=True)

    def __str__(self):
        return f"TLS Cert for {self.common_name} ({self.organization_name})"

    def get_subject(self):
        """Creates an X.509 subject using model fields"""
        return x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, self.country_name),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, self.state_or_province_name),
            x509.NameAttribute(NameOID.LOCALITY_NAME, self.locality_name),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, self.organization_name),
            x509.NameAttribute(NameOID.COMMON_NAME, self.common_name),
        ])

    def save(self, *args, **kwargs):
        """Generates certificate and QR code before saving"""
        if not self.private_key or not self.certificate:
            self.generate_certificate()
        self.generate_qr_code()  # Generate QR code on save
        super().save(*args, **kwargs)

    def generate_certificate(self):
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )

        subject = self.get_subject()

        cert = x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(
            private_key.public_key()).serial_number(
            x509.random_serial_number()).not_valid_before(
            datetime.utcnow()).not_valid_after(
            datetime.utcnow() + timedelta(days=365)
        ).add_extension(
            x509.BasicConstraints(ca=True, path_length=None), critical=True,
        ).sign(private_key, hashes.SHA256())

        self.private_key = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        ).decode()

        self.certificate = cert.public_bytes(serialization.Encoding.PEM).decode()

    def save_certificate_to_files(self, dir_path):
        """Saves the generated certificate and private key to files in the given directory."""

        # Ensure the directory exists
        os.makedirs(dir_path, exist_ok=True)

        cert_file_path = os.path.join(dir_path, f"{self.common_name}.crt")
        key_file_path = os.path.join(dir_path, f"{self.common_name}.key")

        # Save certificate and private key
        with open(cert_file_path, "w") as cert_file:
            cert_file.write(self.certificate)

        with open(key_file_path, "w") as key_file:
            key_file.write(self.private_key)

        print(f"Certificate saved to {cert_file_path}")
        print(f"Private key saved to {key_file_path}")

    def generate_qr_code(self):
        """Generates a QR code from the certificate data"""
        if self.certificate:
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )
            qr.add_data(self.certificate)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")

            buffer = BytesIO()
            img.save(buffer, format="PNG")
            self.qr_code.save(f"qr_{self.common_name}.png", ContentFile(buffer.getvalue()), save=False)
            buffer.close()


class ExternalCertificate(BaseCertificate):
    domain = models.CharField(max_length=255, unique=True)
    email = models.EmailField()
    auth_url = models.URLField(default="http://localhost:8000/.well-known/acme-challenge/")
    cert_dir = models.CharField(max_length=512, default="/home/janek/certbot")
    debug = models.BooleanField(default=False)
    success = models.BooleanField(default=False)
    cert_path = models.CharField(max_length=512, blank=True)
    key_path = models.CharField(max_length=512, blank=True)
    issued_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    def issue_certificate(self):
        import subprocess
        from django.utils import timezone
        import logging
        import os

        logger = logging.getLogger(__name__)

        config_dir = os.path.join(self.cert_dir, "config")
        work_dir = os.path.join(self.cert_dir, "work")
        logs_dir = os.path.join(self.cert_dir, "logs")
        webroot_path = "/home/janek/Desktop/dev/toto/toto/acme-challenges"

        command = ["certbot"]
        if self.debug:
            command.extend(["certonly", "--dry-run"])
        else:
            command.extend(["certonly"])

        command.extend([
            "--webroot",
            "-w", webroot_path,
            "-d", self.domain,
            "--agree-tos",
            "--email", self.email,
            "--non-interactive",
            "--config-dir", config_dir,
            "--work-dir", work_dir,
            "--logs-dir", logs_dir
        ])
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            self.success = True
            self.issued_at = timezone.now()
            self.cert_path = os.path.join(config_dir, "live", self.domain, "fullchain.pem")
            self.key_path = os.path.join(config_dir, "live", self.domain, "privkey.pem")
            logger.info(f"✅ Certificate {'dry run' if self.debug else 'issued'} for {self.domain}")
        except subprocess.CalledProcessError as e:
            self.success = False
            logger.error(f"Failed to {'dry run' if self.debug else 'issue'} certificate for {self.domain}: {e.stderr}")
            print(f"Failed to {'dry run' if self.debug else 'issue'} certificate for {self.domain}: {e.stderr}")

            print(e.stderr)
        else:
            print("OK")
            self.success = True
        self.save()


