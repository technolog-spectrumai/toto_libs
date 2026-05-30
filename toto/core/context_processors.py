from .last_visited import record_and_get_back


def last_visited(request):
    if not request.user.is_authenticated:
        return {}
    back_url, back_name = record_and_get_back(request.user.pk, request.path)
    return {"last_visited_url": back_url, "last_visited_name": back_name}
