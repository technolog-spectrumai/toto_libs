"""
Registry for predefined workflow task nodes.

Apps register tasks in their AppConfig.ready() by importing their
predefined_tasks module. Each task is a callable that receives
input_data (dict) and must return a dict with the same shape as
lambda output: {"data": {...}, "routes": [...]} — routes is optional.
"""

_registry: dict[str, callable] = {}
_celery_registry: dict[str, str] = {}  # task_name -> celery task name


def register(name: str):
    def decorator(fn):
        _registry[name] = fn
        return fn
    return decorator


def register_celery(task_name: str, celery_name: str) -> None:
    _celery_registry[task_name] = celery_name


def get_celery_task(task_name: str) -> str | None:
    return _celery_registry.get(task_name)


def run(name: str, input_data: dict) -> dict:
    if name not in _registry:
        raise ValueError(
            f"Unknown predefined task: {name!r}. "
            f"Available: {sorted(_registry) or '(none registered)'}"
        )
    return _registry[name](input_data)


def available() -> list[str]:
    return sorted(_registry)
