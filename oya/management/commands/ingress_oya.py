import os
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
import os
import django
import json


class Command(BaseCommand):
    help = "Ingress"

    def  handle(self, *args, **options):
        themes = [
            {
                "name": "AlmondLatte",
                "font": "Playfair Display",
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
            },
            {
                "name": "InkwellRonin",
                "font": "Orbitron",
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
                },
                "header": {
                    "light": "border-b-4 border-accent-1 rounded-sm shadow-md",
                    "dark": "border-b-4 border-white rounded-sm shadow-md"
                }
            },
            {
                "name": "Nomadic Donjon",
                "font": "Playfair Display",
                "colors": {
                    "primary-bg-light": "#d8d3c4",
                    "header-bg-light": "#b9b2a3",
                    "appbar-bg-light": "#a59d8f",
                    "bubble-bg-light": "#eae6dc",
                    "text-main-light": "#2e2a25",
                    "primary-bg-dark": "#1a1c1f",
                    "header-bg-dark": "#2b2f34",
                    "appbar-bg-dark": "#3a3f46",
                    "bubble-bg-dark": "#24272b",
                    "text-main-dark": "#dcd6c9",
                    "accent-light": "#6e5c4f",
                    "accent-dark": "#8a7766",
                    "warn-light": "#a88c7f",
                    "warn-dark": "#f0e6d8",
                    "accent-1": "#4b3f35",
                    "accent-2": "#5c4a3e"
                }
            }
        ]

        for theme in themes:
            self.stdout.write(self.style.NOTICE(f"Creating theme: {theme['name']}"))
            call_command(
                "create_theme",
                "--name", theme["name"],
                "--font", theme["font"],
                "--colors", json.dumps(theme["colors"]),
                "--header", json.dumps(theme.get("header", {}))
            )


