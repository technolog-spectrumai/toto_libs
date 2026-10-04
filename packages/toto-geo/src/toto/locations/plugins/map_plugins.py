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
    def get_items(cls, request=None):
        """Every provider's items. A provider that takes ``request`` gets it
        (2026-09-29: so it can leave out what clearances hide from this viewer);
        one written before that is called bare and should offer only what
        everyone may see."""
        import inspect

        items = []
        for provider in cls._providers:
            takes_request = bool(inspect.signature(provider).parameters)
            items.extend(provider(request) if takes_request else provider())
        return items
