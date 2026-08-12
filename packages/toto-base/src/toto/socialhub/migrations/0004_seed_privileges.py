"""Carry the two federal booleans into the institutions that replace them.

Both promised something for years while nothing implemented it, and both are
kept (deprecated) because aurelian reads them by name:

* ``Community.is_federal_tribe`` — *"members are exempt from all poll taxes"*.
  There is a poll tax now, so the promise finally becomes a number: a privilege
  row with ``head_weight = 0``.
* ``Person.is_federal_agent`` — help text about taxes, code about two admin
  views. Those two rights become an **office**, held by whoever held the flag.

The office is created only if somebody actually holds the flag, so an ordinary
host gets no mystery row. If several people hold it, the first becomes the
holder and the rest are listed in the charter rather than silently dropped —
an office has one holder, and quietly discarding the others would lose the fact
that they had the rights yesterday.
"""

from decimal import Decimal

from django.db import migrations


def seed(apps, schema_editor):
    Community = apps.get_model("socialhub", "Community")
    CommunityPrivilege = apps.get_model("socialhub", "CommunityPrivilege")
    Station = apps.get_model("socialhub", "Station")
    Person = apps.get_model("people", "Person")

    for community in Community.objects.filter(is_federal_tribe=True):
        CommunityPrivilege.objects.update_or_create(
            community=community, defaults={"head_weight": Decimal("0")})

    agents = list(Person.objects.filter(is_federal_agent=True).order_by("pk"))
    if not agents:
        return

    charter = (
        "Sees the community chain graph and opens the Administrata view of any "
        "community. Created when Person.is_federal_agent was retired: the flag "
        "granted exactly these two rights."
    )
    if len(agents) > 1:
        others = ", ".join(p.display_name or p.slug for p in agents[1:])
        charter += (
            f" These people also held the flag when it was retired and no "
            f"longer have the rights: {others}."
        )

    Station.objects.update_or_create(
        slug="federal-agent",
        defaults={
            "name": "Federal Agent",
            "charter": charter,
            "holder": agents[0],
            "active": True,
            "may_see_community_chain": True,
            "may_administer_communities": True,
        },
    )


def unseed(apps, schema_editor):
    """Reversible: drop what this created, leave the old flags untouched.

    The flags are the source of truth on the way back, so re-applying this
    rebuilds the same rows.
    """
    Station = apps.get_model("socialhub", "Station")
    CommunityPrivilege = apps.get_model("socialhub", "CommunityPrivilege")

    Station.objects.filter(slug="federal-agent").delete()
    CommunityPrivilege.objects.filter(
        community__is_federal_tribe=True, head_weight=Decimal("0")).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("socialhub", "0003_alter_community_is_federal_tribe_station_and_more"),
        ("people", "0004_deprecate_is_federal_agent"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
