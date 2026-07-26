class LocationContextPlugin:
    """
    Registry for context providers. External apps register callables that
    return dicts; toto.locations merges them into the locations_all context.
    """

    _providers = []

    @classmethod
    def register(cls, func):
        cls._providers.append(func)

    @classmethod
    def get_context(cls):
        context = {}
        for provider in cls._providers:
            context.update(provider())
        return context
