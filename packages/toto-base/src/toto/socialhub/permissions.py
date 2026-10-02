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
