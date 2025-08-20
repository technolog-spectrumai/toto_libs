from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
import json

class Command(BaseCommand):
    help = 'Ingress command to create repo and pages'

    def add_arguments(self, parser):
        parser.add_argument('--repo_url', type=str, default="https://github.com/technolog-spectrumai/websites",help='Git repository URL')
        parser.add_argument('--access_token', type=str, default="???", help='Access token for the repository')
        parser.add_argument('--repo_name', type=str, help='Optional name for the repository')
        parser.add_argument('--branch', type=str, default='main', help='Branch to use')
        parser.add_argument('--username', type=str, default='admin', help='Author username')

    def handle(self, *args, **options):
        repo_url = options['repo_url']
        token = options['access_token']
        repo_name = options.get('repo_name') or repo_url.split('/')[-1].replace('.git', '')
        branch = options['branch']
        username = options['username']

        try:
            # Step 1: Create repository
            self.stdout.write(self.style.NOTICE("Creating repository..."))
            call_command('create_repo', repo_url, token, '--name', repo_name)

            # Step 2: Create SpectrumAI page
            self.stdout.write(self.style.NOTICE("Creating SpectrumAI page..."))

            args_json = json.dumps({
                "site_title": "SpectrumAI",
                "fontawesome_url": "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.7.2/css/all.min.css",
                "logo_dark_url": "https://i.ibb.co/PXwkgwK/octropus-tr.png",
                "logo_light_url": "https://i.ibb.co/KzWrv8Rj/octropus-tr-black.png",
                "logo_alt": "SpectrumAI Logo",
                "brand_name": "SPECTRUM",
                "brand_suffix": "AI",
                "nav_about": "About",
                "nav_portfolio": "Portfolio",
                "nav_contact": "Contact",
                "switch_light": "Switch to Light Mode",
                "switch_dark": "Switch to Dark Mode",
                "hero_title": "Investing in the Future of Intelligence",
                "hero_description": "SpectrumAI is a shareholder in AI-driven startups, focused on innovation, precision, and strategic growth.",
                "features": [
                    { "title": "AI Security", "desc": "Cutting-edge defense intelligence and proactive protection." },
                    { "title": "Cognitive Robotics", "desc": "Teaching autonomous systems to reason and adapt." },
                    { "title": "Medical AI", "desc": "Accelerating diagnostics and precision healthcare." },
                    { "title": "Industrial AI", "desc": "Smart manufacturing and predictive logistics." },
                    { "title": "Financial Innovation", "desc": "Blockchain and decentralized finance systems." },
                    { "title": "Real-Time Analytics", "desc": "Live data pipelines, predictive dashboards, and decision intelligence." }
                ],
                "portfolio_title": "Portfolio",
                "portfolio_description": "We proudly invest in bold ventures redefining autonomy and intelligence. Meet Basilisk Systems.",
                "portfolio_1_url": "https://basilisk-systems.netlify.app/",
                "portfolio_1_image_dark": "https://i.ibb.co/ZzQnJpzC/banner-basilisk.png",
                "portfolio_1_image_light": "https://i.ibb.co/M5RXrcwT/basilisk-light.png",
                "portfolio_1_name": "Basilisk Systems",
                "portfolio_1_tagline": "Smarter Skies Begin Here",
                "portfolio_1_description": "Autonomous drone transport built on vision, security, and relentless innovation—from defense-ready platforms to cloud-integrated navigation.",
                "portfolio_2_url": "https://spontaneous-swan-18b446.netlify.app",
                "portfolio_2_image_dark": "https://i.ibb.co/G4n2fB6G/vo2.png",
                "portfolio_2_image_light": "https://i.ibb.co/mVL6trB6/vo1.png",
                "portfolio_2_name": "Sport Spectrum",
                "portfolio_2_tagline": "Intelligence in Motion",
                "portfolio_2_description": "AI-powered performance analytics for athletes, teams, and sports organizations. From biometric tracking to predictive game strategy, SportSpectrum redefines competitive edge.",
                "visit_text": "Visit Website →",
                "footer_year": "2025",
                "footer_tagline": "Precision. Clarity. Leadership.",
                "contact_label": "Contact us at",
                "contact_email": "technolog@spectrumai.pl"
            })

            call_command(
                'create_page',
                repo_name,
                branch,
                'spectrumai.html',
                'spectrumai',
                args_json,
                '--username',
                username
            )

            self.stdout.write(self.style.SUCCESS("Ingress completed successfully."))

        except CommandError as e:
            self.stderr.write(self.style.ERROR(f"Ingress failed: {e}"))
