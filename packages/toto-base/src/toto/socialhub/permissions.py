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
