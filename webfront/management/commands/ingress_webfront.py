from django.core.management.base import BaseCommand
from django.utils.text import slugify
from django.core.exceptions import ValidationError
from webfront.models import Language, DynamicPage

class Command(BaseCommand):
    help = "Create ingress DynamicPages for Sport, Spectrum, and Basilisk"

    def handle(self, *args, **options):
        language_slug = 'en'
        language, created = Language.objects.get_or_create(
            slug=language_slug,
            defaults={"name": "English"}
        )

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created Language: {language.name}"))
        else:
            self.stdout.write(f"Using existing Language: {language.name}")

        pages = [
            {
                "name": "Sport",
                "template_key": "sport",
                "config_json": {
                    "site": {
                        "title": "Sport Spectrum",
                        "brand_name": "SPECTRUM",
                        "brand_suffix": "AI",
                        "logo_light_url": "https://i.ibb.co/mVL6trB6/vo1.png",
                        "logo_dark_url": "https://i.ibb.co/mVL6trB6/vo1.png",
                        "logo_alt": "Sport Spectrum Logo",
                        "fontawesome_url": "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.7.2/css/all.min.css"
                    },
                    "hero": {
                        "title": "Intelligence in Motion",
                        "description": "AI-powered performance analytics for athletes, teams, and sports organizations."
                    },
                    "features": [
                        { "title": "Biometric Tracking", "desc": "Monitor athlete vitals in real time." },
                        { "title": "Predictive Strategy", "desc": "AI-generated game plans based on historical data." }
                    ]
                }
            },
            {
                "name": "Spectrum",
                "template_key": "spectrum",
                "config_json": {
                    "site": {
                        "title": "SpectrumAI",
                        "brand_name": "SPECTRUM",
                        "brand_suffix": "AI",
                        "logo_light_url": "https://i.ibb.co/KzWrv8Rj/octropus-tr-black.png",
                        "logo_dark_url": "https://i.ibb.co/PXwkgwK/octropus-tr.png",
                        "logo_alt": "SpectrumAI Logo",
                        "fontawesome_url": "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.7.2/css/all.min.css"
                    },
                    "hero": {
                        "title": "Investing in the Future of Intelligence",
                        "description": "Shareholder in AI-driven startups focused on innovation and strategic growth."
                    },
                    "features": [
                        { "title": "AI Security", "desc": "Defense intelligence and proactive protection." },
                        { "title": "Cognitive Robotics", "desc": "Teaching autonomous systems to reason and adapt." }
                    ]
                }
            },
            {
                "name": "Basilisk",
                "template_key": "basilisk",
                "config_json": {
                    "site": {
                        "title": "Basilisk Systems",
                        "brand_name": "BASILISK",
                        "brand_suffix": "SYS",
                        "logo_light_url": "https://i.ibb.co/M5RXrcwT/basilisk-light.png",
                        "logo_dark_url": "https://i.ibb.co/ZzQnJpzC/banner-basilisk.png",
                        "logo_alt": "Basilisk Logo",
                        "fontawesome_url": "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.7.2/css/all.min.css"
                    },
                    "hero": {
                        "title": "Smarter Skies Begin Here",
                        "description": "Autonomous drone transport built on vision, security, and relentless innovation."
                    },
                    "features": [
                        { "title": "Cloud Navigation", "desc": "Integrated drone routing via secure cloud systems." },
                        { "title": "Defense Ready", "desc": "Built for tactical deployment and surveillance." }
                    ]
                }
            }
        ]

        for page in pages:
            slug = slugify(page["name"])
            dp = DynamicPage(
                name=page["name"],
                slug=slug,
                language=language,
                template_key=page["template_key"],
                config_json=page["config_json"]
            )
            try:
                dp.full_clean()
                dp.save()
                self.stdout.write(self.style.SUCCESS(f"Created DynamicPage: {dp.name}"))
            except ValidationError as e:
                self.stderr.write(f"Validation failed for {dp.name}: {e}")
