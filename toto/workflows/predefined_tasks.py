"""
Registry for predefined workflow task nodes.

Apps register tasks in their AppConfig.ready() by importing their
predefined_tasks module. Each task is a callable that receives
input_data (dict) and must return a dict with the same shape as
lambda output: {"data": {...}, "routes": [...]} — routes is optional.
"""

_registry: dict[str, callable] = {}


def register(name: str):
    def decorator(fn):
        _registry[name] = fn
        return fn
    return decorator


def run(name: str, input_data: dict) -> dict:
    if name not in _registry:
        raise ValueError(
            f"Unknown predefined task: {name!r}. "
            f"Available: {sorted(_registry) or '(none registered)'}"
        )
    return _registry[name](input_data)


def available() -> list[str]:
    return sorted(_registry)
