from django.core.cache import cache

_TIMEOUT = 60 * 60 * 24 * 30  # 30 days
_KEY = "capitol:community:{}"


def set_community(user_pk: int, slug: str) -> None:
    cache.set(_KEY.format(user_pk), slug, _TIMEOUT)


def get_community_slug(user_pk: int) -> str:
    return cache.get(_KEY.format(user_pk), "")
