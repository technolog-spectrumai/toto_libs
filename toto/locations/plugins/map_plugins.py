class LocationMapPlugin:
    """
    Registry for map item providers. External apps register callables that
    return lists of location-dict items; toto.locations merges them into the
    map payload without importing from studio.*.
    """

    _providers = []

    @classmethod
    def register(cls, func):
        cls._providers.append(func)

    @classmethod
    def get_items(cls):
        items = []
        for provider in cls._providers:
            items.extend(provider())
        return items
