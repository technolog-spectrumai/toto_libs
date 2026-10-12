"""Discipline's starting state: the auras the host's file names, given to
the communities it names, and a first balance for accounts made before the
app was installed.

Idempotent. An aura that exists keeps the numbers an administrator gave it
in the admin panel; a community that has an aura keeps it. The file is
checked first and a bad one stops the run, which makes ``ingress_all`` the
deploy-time leg of the check ``manage.py check`` makes.
"""

from django.core.management.base import CommandError

from toto.ingress import IngressCommand


class Command(IngressCommand):
    help = "Seed Discipline: the file's auras, and a first balance for existing accounts."

    bootstrap_economy = False

    def process(self):
        from django.contrib.auth import get_user_model

        from toto.discipline import config, services
        from toto.discipline.models import Aura, CommunityAura, ManaAccount
        from toto.socialhub.models import Community

        problems = config.validate()
        if problems:
            raise CommandError("the Discipline file is not usable:\n  - " + "\n  - ".join(problems))
        numbers = config.load(force=True)

        for entry in numbers.auras:
            aura, made = Aura.objects.get_or_create(slug=entry["slug"], defaults={
                "name": entry["name"], "description": entry.get("description", ""),
                "regen_per_hour": config._number(entry["regen_per_hour"]),
                "cap": None if entry.get("cap") is None else config._number(entry["cap"])})
            self.stdout.write(f"  {'+' if made else '✓'} aura {aura.slug}"
                              + ("" if made else " (kept)"))
            for slug in entry.get("communities") or []:
                community = Community.objects.filter(slug=slug).first()
                if community is None:
                    self.stdout.write(f"    - no community {slug}: its aura waits for it")
                    continue
                if CommunityAura.objects.filter(community=community).exists():
                    self.stdout.write(f"    ✓ {slug} has an aura (kept)")
                    continue
                CommunityAura.objects.create(community=community, aura=aura)
                self.stdout.write(f"    + {slug} has the aura {aura.slug}")

        without = get_user_model().objects.exclude(
            pk__in=ManaAccount.objects.values("user_id"))
        started = 0
        for user in without:
            services.signup(user)
            started += 1
        self.stdout.write(f"discipline: {started} account(s) given their first balance, "
                          f"{Aura.objects.count()} aura(s)")
