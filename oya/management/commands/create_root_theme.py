from django.core.management.base import BaseCommand
from oya.models import Font, Theme


class Command(BaseCommand):
    help = "Create radically contrastive fonts and themes: MaritimeDonjon, AlmondLatte, ElegantSpectrum"

    def handle(self, *args, **kwargs):
        # Step 1: Fonts
        font_data = {
            "Roboto": "https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap",
            "Playfair Display": "https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700&display=swap",
            "Orbitron": "https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700&display=swap"
        }

        fonts = {}
        for name, cdn in font_data.items():
            font, created = Font.objects.get_or_create(name=name, defaults={"cdn_link": cdn})
            fonts[name] = font
            status = "Created" if created else "Already exists"
            self.stdout.write(self.style.SUCCESS(f"{status} Font: {name}"))

        # 🚢 MaritimeDonjon – high drama coastal
        maritime_colors = {
            "colors": {
                "primary-bg-light": "#ffffff",
                "header-bg-light": "#e0f2ff",
                "appbar-bg-light": "#aad4f5",
                "bubble-bg-light": "#f6fbff",
                "text-main-light": "#000000",
                "primary-bg-dark": "#0a121e",
                "header-bg-dark": "#142a42",
                "appbar-bg-dark": "#1e3d57",
                "bubble-bg-dark": "#0f1929",
                "text-main-dark": "#ffffff",
                "accent-light": "#003c71",
                "accent-dark": "#9de4ff",
                "warn-light": "#ff2400",
                "warn-dark": "#ffdfd6",
                "accent-1": "#118f00",
                "accent-2": "#ffe600"
            }
        }

        theme1, created1 = Theme.objects.get_or_create(
            name="MaritimeDonjon",
            defaults={"theme": maritime_colors, "font": fonts["Roboto"]}
        )
        self.stdout.write(self.style.SUCCESS(f"{'Created' if created1 else 'Already exists'} Theme: MaritimeDonjon"))

        # ☕ AlmondLatte – cozy clarity
        almond_colors = {
            "colors": {
                "primary-bg-light": "#fefaf7",
                "header-bg-light": "#f2e8df",
                "appbar-bg-light": "#e2d0bb",
                "bubble-bg-light": "#fff9f3",
                "text-main-light": "#1a110c",
                "primary-bg-dark": "#1a1a1a",
                "header-bg-dark": "#292929",
                "appbar-bg-dark": "#3a3a3a",
                "bubble-bg-dark": "#0c0c0c",
                "text-main-dark": "#ffffff",
                "accent-light": "#a86200",
                "accent-dark": "#ffd480",
                "warn-light": "#c92f0f",
                "warn-dark": "#ffe9e3",
                "accent-1": "#823c00",
                "accent-2": "#007c73"
            }
        }

        theme2, created2 = Theme.objects.get_or_create(
            name="AlmondLatte",
            defaults={"theme": almond_colors, "font": fonts["Playfair Display"]}
        )
        self.stdout.write(self.style.SUCCESS(f"{'Created' if created2 else 'Already exists'} Theme: AlmondLatte"))

        # 🎩 ElegantSpectrum – minimalist precision
        spectre_colors = {
            "colors": {
                "primary-bg-light": "#ffffff",
                "header-bg-light": "#efefef",
                "appbar-bg-light": "#dbdbdb",
                "bubble-bg-light": "#f8f8f8",
                "text-main-light": "#000000",
                "primary-bg-dark": "#000000",
                "header-bg-dark": "#1a1a1a",
                "appbar-bg-dark": "#2f2f2f",
                "bubble-bg-dark": "#0a0a0a",
                "text-main-dark": "#ffffff",
                "accent-light": "#a00020",
                "accent-dark": "#ff3b5c",
                "warn-light": "#ff8800",
                "warn-dark": "#fff0cc",
                "accent-1": "#444c56",
                "accent-2": "#c8d1db"
            }
        }

        theme3, created3 = Theme.objects.get_or_create(
            name="ElegantSpectrum",
            defaults={"theme": spectre_colors, "font": fonts["Roboto"]}
        )
        self.stdout.write(self.style.SUCCESS(f"{'Created' if created3 else 'Already exists'} Theme: ElegantSpectrum"))

        inkwell_colors = {
            "colors": {
                "primary-bg-light": "#fcfcfc",
                "header-bg-light": "#eeeeee",
                "appbar-bg-light": "#dcdcdc",
                "bubble-bg-light": "#f6f6f6",
                "text-main-light": "#101010",

                "primary-bg-dark": "#080808",
                "header-bg-dark": "#111111",
                "appbar-bg-dark": "#1a1a1a",
                "bubble-bg-dark": "#0a0a0a",
                "text-main-dark": "#f5f5f5",

                "accent-light": "#5661a8",
                "accent-dark": "#4dc1d2",
                "warn-light": "#905a78",
                "warn-dark": "#d3cad9",
                "accent-1": "#1f1f2b",
                "accent-2": "#a4acc4"
            }
        }

        inkwell_header = {
            "border": "border-b-4",
            "color_light": "border-accent-1",
            "color_dark": "border-white",
            "radius": "rounded-sm",
            "shadow": "shadow-md"
        }

        theme_ronin, created = Theme.objects.get_or_create(
            name="InkwellRonin",
            defaults={
                "theme": inkwell_colors,
                "font": fonts["Orbitron"],
                "header": inkwell_header
            }
        )


        self.stdout.write(self.style.SUCCESS(f"{'Created' if created else 'Already exists'} Theme: InkwellRonin"))

        banzai_colors = {
            "colors": {
                "primary-bg-light": "#fef9f8",
                "header-bg-light": "#fdecea",
                "appbar-bg-light": "#fbd4d2",
                "bubble-bg-light": "#fff5f5",
                "text-main-light": "#1c0a0a",

                "primary-bg-dark": "#0b0404",
                "header-bg-dark": "#1f0b0b",
                "appbar-bg-dark": "#330d0d",
                "bubble-bg-dark": "#160606",
                "text-main-dark": "#fefefe",

                "accent-light": "#d30000",
                "accent-dark": "#ff5959",
                "warn-light": "#ff2e00",
                "warn-dark": "#ffe4e4",
                "accent-1": "#840000",
                "accent-2": "#DADBDC"
            }
        }

        banzai_header = {
            "border": "border-b-4",
            "color_light": "border-accent-1",
            "color_dark": "border-accent-2",
            "radius": "rounded-md",
            "shadow": "shadow-lg"
        }

        theme_banzai, created = Theme.objects.get_or_create(
            name="Cyber Banzai",
            defaults={
                "theme": banzai_colors,
                "font": fonts["Orbitron"],
                "header": banzai_header
            }
        )

        self.stdout.write(self.style.SUCCESS(f"{'Created' if created else 'Already exists'} Theme: Banzai"))





