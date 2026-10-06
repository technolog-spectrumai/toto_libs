"""What is a company, who sees its register, who keeps it (stage 65).

One module, asked by the page's sections, the profile's section and every
door, so a page and a door cannot disagree.

    a company       a community whose ``org_type`` is ``company``; a
                    community that stops being one keeps its rows and shows
                    none of them
    sees            every signed-in viewer of the company's page, as its
                    members list; a visitor nothing
    manages         the community's head, or an administrator
                    (``socialhub.permissions.may_moderate_community``: a
                    real superuser on the Superuser plan); staff alone is
                    not enough, and neither is being a senior member

Holding shares is none of these: a shareholder who is no member is no
member, sees what any signed-in viewer sees and manages nothing.
"""

from __future__ import annotations


def is_company(community) -> bool:
    from toto.socialhub.models import Community

    return community is not None and community.org_type == Community.COMPANY


def may_see_register(user, community) -> bool:
    """The ID number, the register and its total."""
    return bool(getattr(user, "is_authenticated", False)) and is_company(community)


def may_manage(user, community) -> bool:
    """Sets the ID number and records, changes and removes holdings."""
    from toto.socialhub.permissions import may_moderate_community

    return is_company(community) and may_moderate_community(user, community)


def visible_companies(user):
    """The communities whose register ``user`` may see, as a queryset: every
    company for a signed-in viewer, none for a visitor. The profile's
    section filters a person's holdings by it."""
    from toto.socialhub.models import Community

    companies = Community.objects.filter(org_type=Community.COMPANY)
    return companies if getattr(user, "is_authenticated", False) else companies.none()
