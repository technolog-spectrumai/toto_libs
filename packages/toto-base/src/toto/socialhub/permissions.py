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

    return community.senior_members.filter(pk=person.pk).exists()
