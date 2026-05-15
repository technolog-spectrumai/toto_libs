from toto.locations.plugins.context_plugins import LocationContextPlugin


def _map_travels_context():
    from toto.travels.models import Travel, Visit

    recent_travels = list(
        Travel.objects
        .select_related("route")
        .order_by("-starts_at")[:5]
    )
    recent_visits = list(
        Visit.objects
        .select_related("participant", "location")
        .order_by("-visited_at")[:5]
    )
    return {
        "map_recent_travels": recent_travels,
        "map_recent_visits": recent_visits,
    }


LocationContextPlugin.register(_map_travels_context)
