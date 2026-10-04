class LocationUrlPlugin:
    """
    Generic URL resolver registry. External apps register named callables at
    startup; toto.locations calls get_url(name, ...) without importing studio.*.
    """

    _resolvers = {}

    @classmethod
    def register(cls, name, func):
        cls._resolvers[name] = func

    @classmethod
    def get_url(cls, name, *args, **kwargs):
        resolver = cls._resolvers.get(name)
        if resolver:
            return resolver(*args, **kwargs)
        return None

    # Backward-compatibility shims
    @classmethod
    def register_address_visit_review(cls, func):
        cls.register("address_visit_review", func)

    @classmethod
    def get_address_visit_review_url(cls, pk):
        return cls.get_url("address_visit_review", pk)
