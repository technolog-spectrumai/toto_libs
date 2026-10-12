from toto.people.models import Person


def current_person(request):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return None

    person = getattr(request.user, "community_profile", None)
    if person is not None:
        return person

    return Person.objects.filter(user=request.user).first()


def can_manage_community_news(request, community):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return False

    if request.user.is_superuser or request.user.is_staff:
        return True

    person = current_person(request)
    if person is None:
        return False

    if community.head_id == person.pk:
        return True

    if community.senior_members.filter(pk=person.pk).exists():
        return True

    # A community privilege reaches ACROSS communities: membership of any
    # community granting may_manage_community_news publishes anywhere. Checked
    # last because it is the rarest and costs a query.
    from toto.socialhub import privileges

    return privileges.has_privilege(request.user, "may_manage_community_news")


def can_manage_some_community_news(request):
    """Whether the sender may write the news of at least one community
    (2026-10-02): ``can_manage_community_news`` itself, asked of each
    community until one says yes.

    For a door that serves every community's news at once and cannot tell
    which one a request is for: the Trix editor's attachment upload, which a
    picture reaches before the post it goes into exists. Asked, not
    restated — that door once had a rule of its own, a model permission no
    code grants, and shut out the heads and senior members who write news.

    The community the sender heads or sits among the seniors of, if any, is
    asked first, so a writer is answered there; the others are asked only for
    a sender with none, whom the rule's other reasons (staff, a superuser,
    the privilege) answer at the first. No community, no news to write.
    """
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return False

    from django.db.models import Q

    from toto.socialhub.models import Community

    communities = Community.objects.order_by("pk")
    person = current_person(request)
    if person is not None:
        own = communities.filter(Q(head=person) | Q(senior_members=person)).first()
        if own is not None and can_manage_community_news(request, own):
            return True
    return any(can_manage_community_news(request, community)
               for community in communities.iterator())


def is_community_member(user, community) -> bool:
    """Does ``user`` belong to ``community`` (2026-10-06)? Among its members
    (``Person.communities``), or its head, or one of its senior members: the
    head and the seniors are not always among the members."""
    if not getattr(user, "is_authenticated", False) or community is None:
        return False
    person = Person.objects.filter(user=user).first()
    if person is None:
        return False
    if community.head_id == person.pk:
        return True
    if person.communities.filter(pk=community.pk).exists():
        return True
    return community.senior_members.filter(pk=person.pk).exists()


def may_moderate_community(user, community) -> bool:
    """May ``user`` decide for ``community`` (2026-10-06): its head, or an
    administrator (``contact_access.is_administrator``: a real superuser on
    the Superuser plan where the host sells it). Staff alone is not enough,
    and neither is being a senior member."""
    if not getattr(user, "is_authenticated", False) or community is None:
        return False
    from toto.socialhub.contact_access import is_administrator

    if is_administrator(user):
        return True
    person = Person.objects.filter(user=user).first()
    return person is not None and community.head_id == person.pk


# ---------------------------------------------------------------------------
# Private communities (2026-10-12)
# ---------------------------------------------------------------------------
# The owner: "now we need private communities - only after you are in
# yourself you can see who is in the community. What is public instead is
# number of members." A host turns it on with SOCIALHUB_PRIVATE_COMMUNITIES;
# off (the default) every signed-in member reads every community's members,
# as before. One rule for the page, the chart, the API and the chips on a
# profile, so no listing publishes what the page keeps.

def private_communities() -> bool:
    from django.conf import settings

    return bool(getattr(settings, "SOCIALHUB_PRIVATE_COMMUNITIES", False))


def may_see_members(user, community) -> bool:
    """May ``user`` read who is in ``community``? Where communities are
    private: its members, its head and an administrator. The number of
    members is everybody's and is not asked here."""
    if not private_communities():
        return True
    if not getattr(user, "is_authenticated", False) or community is None:
        return False
    from toto.socialhub.contact_access import is_administrator

    if is_administrator(user):
        return True
    person = Person.objects.filter(user=user).first()
    if person is None:
        return False
    return community.head_id == person.pk or person.communities.filter(pk=community.pk).exists()


def communities_shown_to(user, queryset=None):
    """The communities whose membership ``user`` may read, of ``queryset``
    (every community without one): what a profile's chips are drawn from, so
    somebody's profile names only the communities the viewer shares with
    them (their own profile names all of theirs, by the same rule)."""
    from toto.socialhub.models import Community

    queryset = Community.objects.all() if queryset is None else queryset
    if not private_communities():
        return queryset
    if not getattr(user, "is_authenticated", False):
        return queryset.none()
    from django.db.models import Q

    from toto.socialhub.contact_access import is_administrator

    if is_administrator(user):
        return queryset
    person = Person.objects.filter(user=user).first()
    if person is None:
        return queryset.none()
    return queryset.filter(Q(members=person) | Q(head=person)).distinct()

