import os
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
import os
import django

class Command(BaseCommand):
    help = "Initialize platform by clearing the database, running migrations, generating TLS certificate, creating an address, setting up config, and creating a superuser"

    def handle(self, *args, **options):
        try:
            self.run()
        except CommandError as e:
            self.stderr.write(self.style.ERROR(f"Error initializing platform: {e}"))

    def auto_create_migrations(self):
        # Set up Django environment
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'your_project_name.settings')
        django.setup()

        # Loop through installed apps and run makemigrations
        for app in settings.INSTALLED_APPS:
            try:
                if app.find('django.contrib') != -1:
                    if app.find('auth') != -1:
                        call_command('makemigrations', 'auth')
                    else:
                        continue
                else:
                    call_command('makemigrations', app)
            except Exception as e:
                print(f"Skipping {app}: {e}")

    def run(self):
        self.clear_db()

        self.stdout.write(self.style.NOTICE("Running migrations..."))
        call_command("makemigrations")
        call_command("migrate")
        self.auto_create_migrations()
        call_command("makemigrations")
        call_command("migrate")
        self.stdout.write(self.style.SUCCESS("Migrations completed."))

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

        domain = "spectrumai.pl"
        self.stdout.write(self.style.NOTICE("Creating TLS certificate..."))
        call_command("create_tls_certificate", domain, company_name, str(latest_address_id))
        self.stdout.write(self.style.SUCCESS("TLS certificate created."))

        latest_tls_cert_id = self.get_latest_tls_cert_id()
        if latest_tls_cert_id is None:
            self.stderr.write(self.style.ERROR("No TLS certificate found. Initialization aborted."))
            return

        self.stdout.write(self.style.NOTICE("Creating fonts and theme..."))
        call_command("create_root_theme")
        self.stdout.write(self.style.SUCCESS("Fonts and theme created."))

        self.stdout.write(self.style.NOTICE("Creating superuser..."))
        call_command("create_user", "admin", "admin", admin=True)
        self.stdout.write(self.style.SUCCESS("Superuser created."))

        self.stdout.write(self.style.NOTICE("Creating homepage..."))
        call_command("create_page")
        self.stdout.write(self.style.SUCCESS("Homepage created."))

        latest_theme_id = self.get_latest_theme_id()
        latest_page = self.get_latest_page()

        site_name = "TOTO Community Platform"
        self.stdout.write(self.style.NOTICE("Creating platform..."))

        create_platform_args = [
            site_name,
            domain,
            str(latest_company_id),
            str(latest_tls_cert_id),
            "--active=True"
        ]

        if latest_theme_id:
            create_platform_args.append(f"--theme_id={latest_theme_id}")

        if latest_page:
            homepage_url = "netogami/pages/" + latest_page.slug + "/" + latest_page.language
            create_platform_args.append(f"--index_url={homepage_url}")
        else:
            self.stdout.write(self.style.WARNING("No homepage found — platform will be created without index_url."))

        call_command("create_platform", *create_platform_args)
        self.stdout.write(self.style.SUCCESS("Platform created."))

        call_command("create_platform", *create_platform_args)
        self.stdout.write(self.style.SUCCESS("Platform created."))


        self.stdout.write(self.style.NOTICE("Saving TLS certificate files..."))
        cert_dir = os.path.abspath(os.path.join("..", "cert"))
        call_command("save_tls_certificate", domain, cert_dir)
        self.stdout.write(self.style.SUCCESS("TLS certificate files saved successfully."))

        self.stdout.write(self.style.NOTICE("Creating default dashboard blocks..."))
        call_command("create_dashboard_block", "Forum",
                     "--description=Engage in discussions with the community.",
                     "--icon=fas fa-comments", "--link=/nest/not-implemented/")
        call_command("create_dashboard_block", "Profile",
                     "--description=Manage your personal information and settings.",
                     "--icon=fas fa-user", "--link=/nest/profile/")
        self.stdout.write(self.style.SUCCESS("Dashboard blocks created successfully."))

        # self.stdout.write(self.style.NOTICE("Creating theme..."))
        # theme_name = "blue_elf"
        # call_command("create_theme", theme_name)
        #
        # blog_site_name = "kodama"
        # self.stdout.write(self.style.NOTICE("Creating SiteConfig..."))
        # call_command("create_site", blog_site_name, theme_name)
        # self.stdout.write(self.style.SUCCESS(f"SiteConfig created successfully: {blog_site_name}"))
        #
        # self.stdout.write(self.style.NOTICE("Creating default articles and categories..."))
        # call_command("create_content", blog_site_name)
        # self.stdout.write(self.style.SUCCESS("Default content created successfully."))
        #
        # self.stdout.write(self.style.NOTICE("Creating test user..."))
        # call_command("create_user", "janek", "janek", admin=False)
        #
        # self.stdout.write(self.style.NOTICE("Creating test page..."))
        # call_command("create_page")
        #
        # self.stdout.write(self.style.NOTICE("Creating sources..."))
        # call_command("create_sources")


    def clear_db(self):
        """Removes the existing database file if present"""
        db_path = settings.DATABASES.get("default", {}).get("NAME")
        if db_path and os.path.exists(db_path):
            self.stdout.write(self.style.WARNING("Removing existing database..."))
            os.remove(db_path)
            self.stdout.write(self.style.SUCCESS("Database removed successfully."))

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
        from oya.models import Address  # Import inside method to avoid potential issues
        latest_address = Address.objects.order_by("-id").first()
        return latest_address.id if latest_address else None

    def get_latest_tls_cert_id(self):
        """Fetches the latest TLS Certificate ID to be used in platform creation"""
        from oya.models import TLSCertificate  # Import inside method to avoid potential issues
        latest_cert = TLSCertificate.objects.order_by("-id").first()
        return latest_cert.id if latest_cert else None

    def get_latest_company_id(self):
        """Fetches the latest Company ID to be used in platform creation"""
        from oya.models import Company  # Import locally to avoid circular imports
        latest_company = Company.objects.order_by("-id").first()
        return latest_company.id if latest_company else None

    def get_latest_theme_id(self):
        """Fetches the latest Theme ID to be used in platform creation"""
        from oya.models import Theme  # Import locally to avoid circular imports
        latest_theme = Theme.objects.order_by("-id").first()
        return latest_theme.id if latest_theme else None

    def get_latest_page(self):
        from netogami.models import Page
        latest_page = Page.objects.order_by("-created_at").first()
        return latest_page


