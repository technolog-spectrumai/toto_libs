from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from netogami.models import Template, Page
from oya.models import Platform

class Command(BaseCommand):
    help = "Create a demo Tailwind+Alpine.js-based Template and Page"

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Creating Tailwind/Alpine.js template..."))

        # Full HTML content including head and body
        full_content = """
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{{ title }}</title>
            <script src="https://cdn.tailwindcss.com"></script>
            <script src="https://unpkg.com/alpinejs" defer></script>
        </head>
        <body>
            <div class="min-h-screen bg-gray-100 flex items-center justify-center p-6">
                <div class="max-w-md w-full bg-white shadow-md rounded p-6" x-data="{ open: false }">
                    <h1 class="text-2xl font-bold mb-4">{{ title }}</h1>
                    <p class="text-gray-700 mb-4">{{ description }}</p>
                    <button 
                        class="bg-blue-500 text-white px-4 py-2 rounded hover:bg-blue-600 transition"
                        @click="open = !open">
                        Toggle Info
                    </button>
                    <div x-show="open" class="mt-4 text-sm text-gray-600">
                        <p>{{ extra_info }}</p>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """

        template, _ = Template.objects.get_or_create(
            name="Tailwind Demo",
            defaults={
                "description": "A responsive template using Tailwind and Alpine.js",
                "content": full_content.strip(),
            }
        )

        # Use the first superuser or any user as the author
        user = User.objects.filter(is_superuser=True).first() or User.objects.first()
        if not user:
            self.stdout.write(self.style.ERROR("No users found to assign as author."))
            return

        page_data = {
            "title": "Welcome to Netogami",
            "description": "This page was generated using Tailwind CSS and Alpine.js.",
            "extra_info": "Dynamic UI with Alpine.js is just a click away!"
        }

        page, created = Page.objects.get_or_create(
            template=template,
            author=user,
            language="en",
            defaults={"data": page_data}
        )

        for platform in Platform.objects.filter(active=True):
            platform.index_url = page.get_url()
            platform.save()
            self.stdout.write(self.style.SUCCESS(
                f"Updated Platform '{platform.site_name}' index_url to: {platform.index_url}"
            ))

        self.stdout.write(self.style.SUCCESS(
            f"{'Created' if created else 'Updated'} page: {page.slug} for user {user.username}"
        ))
