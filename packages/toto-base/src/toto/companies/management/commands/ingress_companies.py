"""The sample company (2026-10-06): ``gizmo.inc``, with a share register.

The ONE sample row the compulsory (``realistic``) seed makes, and ``full``
makes it too: a company to open the Shareholdings tab on, on a platform
that would otherwise have none. Everything else a realistic seed makes is a
row the platform needs.

What it makes, once per database:

* a community named ``gizmo.inc`` whose kind is ``company`` (its slug is
  what the model derives from the name: ``gizmoinc``). Its head is the
  admin's person, and only where that account is an administrator already
  (``socialhub.contact_access.is_administrator``), so heading the company
  gives it nothing it had not; otherwise the company has no head;
* its ``CompanyRecord``, with a sample ID number;
* holdings for PERSONS THAT EXIST at that moment and have an active
  account: at most four, the admin's first and then by primary key, with
  500, 300, 150 and 50 shares (the first N of them for N persons); one
  person alone holds 1000. No person and no account is ever made here, and
  with no person at all the register starts empty.

Nobody is made a member: a signed-in viewer sees a company's register
whether a member or not (``access.may_see_register``), and a holding gives
nothing of the community. No position is made either: the organisation
chart is the socialhub's, and this app does not read it.

The seed runs at every start of a container, so it remembers that it ran
(``core.BootstrapMarker``, ``sample-company:gizmo.inc``) and then does
nothing at all: a company an administrator deleted stays deleted, a holding
they changed or removed stays so, and a person who joins later is given no
shares. Before that mark it only ever creates what is missing and never
changes a row that is there. A community of that name that is no company
is left alone, and the mark says the name was taken.

The audit chain gets the records the doors would write (the number, each
holding recorded) with NO actor: the seed made them, not the admin.

``SEED_SAMPLE_COMPANY=0`` in the environment keeps the company out.

    manage.py ingress_companies --mode realistic
"""

import os

from django.db import transaction

from toto.ingress import IngressCommand

NAME = "gizmo.inc"
ID_NUMBER = "0000123456"
#: Shares of the first, second, third and fourth holder.
QUANTITIES = (500, 300, 150, 50)
#: What one person alone holds.
ALONE = 1000
MARKER = "sample-company:gizmo.inc"
SEEDED, NAME_TAKEN = "seeded", "name taken"
SWITCH = "SEED_SAMPLE_COMPANY"
OFF = ("0", "false", "no", "off")


def holders(admin_person=None) -> list:
    """Who gets a holding: the persons with an active account, the admin's
    first and then by primary key, at most as many as ``QUANTITIES``."""
    from toto.people.models import Person

    people = list(Person.objects.filter(user__is_active=True).order_by("pk"))
    if admin_person is not None and admin_person in people:
        people.remove(admin_person)
        people.insert(0, admin_person)
    return people[:len(QUANTITIES)]


def quantities(count: int) -> tuple:
    return (ALONE,) if count == 1 else QUANTITIES[:count]


class Command(IngressCommand):
    help = ("Seed gizmo.inc, a sample company: it shows the share register "
            "and the organisation chart.")

    def process(self):
        if os.environ.get(SWITCH, "1").strip().lower() in OFF:
            self.stdout.write(f"companies: no sample company ({SWITCH} is off)")
            return
        with transaction.atomic():
            self.seed()

    def seed(self):
        from toto.companies import audit
        from toto.companies.models import CompanyRecord, ShareHolding
        from toto.core.models import BootstrapMarker
        from toto.people.models import Person
        from toto.socialhub.contact_access import is_administrator
        from toto.socialhub.models import Community

        marker = BootstrapMarker.objects.filter(name=MARKER).first()
        if marker is not None:
            self.stdout.write(f"companies: sample company {NAME} was settled at an earlier "
                              f"start ({marker.value}); nothing to do")
            return

        named = list(Community.objects.filter(name=NAME).order_by("pk"))
        community = next((one for one in named if one.org_type == Community.COMPANY), None)
        if community is None and named:
            BootstrapMarker.objects.create(name=MARKER, value=NAME_TAKEN)
            self.stdout.write(self.style.WARNING(
                f"⚠ A community named {NAME} exists and is no company: left alone, "
                "no sample company made."))
            return

        admin_username = os.environ.get("ADMIN_USERNAME", "admin")
        admin_person = Person.objects.filter(user__username=admin_username).first()
        if community is None:
            head = (admin_person if admin_person is not None
                    and is_administrator(admin_person.user) else None)
            community = Community.objects.create(name=NAME, org_type=Community.COMPANY,
                                                 head=head)
            self.stdout.write(self.style.SUCCESS(
                f"✔ Created sample company: {NAME} (slug {community.slug}, "
                f"head: {head.display_name if head is not None else 'nobody'})"))
        else:
            self.stdout.write(self.style.WARNING(f"⚠ Company already exists: {NAME}"))

        _record, made = CompanyRecord.objects.get_or_create(
            community=community, defaults={"id_number": ID_NUMBER})
        if made:
            audit.number_changed(None, community, before="", after=ID_NUMBER)
            self.stdout.write(self.style.SUCCESS(f"✔ Company ID number of {NAME}: {ID_NUMBER}"))

        people = holders(admin_person)
        if not people:
            self.stdout.write(self.style.WARNING(
                f"⚠ No person with an active account exists: the register of {NAME} "
                "starts empty."))
        for person, quantity in zip(people, quantities(len(people))):
            holding, made = ShareHolding.objects.get_or_create(
                community=community, person=person, defaults={"quantity": quantity})
            if made:
                audit.holding(audit.HOLDING_RECORDED, None, community, person,
                              holding_id=holding.pk, after=quantity)
                self.stdout.write(self.style.SUCCESS(
                    f"✔ {person.display_name} holds {quantity} shares of {NAME}"))
        BootstrapMarker.objects.create(name=MARKER, value=SEEDED)
