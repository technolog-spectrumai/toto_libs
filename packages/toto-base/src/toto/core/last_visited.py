from django.core.cache import cache

_TIMEOUT = 60 * 60 * 12  # 12 hours
_KEY = "last_visited:{}"

# Ordered list of (url_prefix, display_name) for meaningful app sections.
_APPS = [
    ("/vault/", "Vault"),
    ("/socialhub/", "Community"),
    ("/events/", "Events"),
    ("/kanban/", "Tasks"),
    ("/memo/", "Presentations"),
    ("/robots/", "Robots"),
    ("/transcription/", "Transcription"),
    ("/vod/", "VOD"),
    ("/polls/", "Polls"),
]


def _match(path: str):
    for prefix, name in _APPS:
        if path.startswith(prefix):
            return prefix, name
    return None, None


def record_and_get_back(user_pk: int, current_path: str):
    """
    Record current path in cache and return (back_url, back_name) for the
    previous meaningful app if it differs from the current one.
    Returns (None, None) when there is nothing useful to link back to.
    """
    current_prefix, current_name = _match(current_path)
    key = _KEY.format(user_pk)
    stored = cache.get(key)

    back_url = back_name = None
    if stored and stored.get("prefix") != current_prefix:
        back_url = stored["url"]
        back_name = stored["name"]

    if current_prefix:
        cache.set(key, {"url": current_path, "prefix": current_prefix, "name": current_name}, _TIMEOUT)

    return back_url, back_name
