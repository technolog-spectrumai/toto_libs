from django.utils.text import slugify

from toto.competence.models import SkillBadge, SkillBadgePrerequisite, SkillGroup
from toto.ingress import IngressCommand


class Command(IngressCommand):
    help = "Seeds demo competence skill groups and badges."

    def process(self):
        if not self.full:
            return

        tree, _ = SkillGroup.objects.get_or_create(
            slug="python-developer",
            defaults={
                "title": "Python Developer",
                "description": "A simple Python and Django skill tree.",
                "order": 1,
            },
        )

        badge_specs = [
            ("Python Basics", "Core Python syntax and control flow.", "fa-solid fa-code"),
            ("Django Basics", "Models, views, templates, and URLs.", "fa-solid fa-server"),
            ("API Basics", "Simple backend APIs and integrations.", "fa-solid fa-network-wired"),
        ]

        badges = []
        for order, (title, description, icon) in enumerate(badge_specs, start=1):
            badge, _ = SkillBadge.objects.get_or_create(
                group=tree,
                slug=slugify(title),
                defaults={
                    "title": title,
                    "description": description,
                    "icon": icon,
                    "order": order,
                },
            )
            badges.append(badge)

        for badge, prerequisite in [
            (badges[1], badges[0]),
            (badges[2], badges[1]),
        ]:
            SkillBadgePrerequisite.objects.get_or_create(
                badge=badge,
                prerequisite=prerequisite,
            )

        print("[Ingress] Demo competence skill tree created successfully.")
