"""
Registry for predefined workflow task nodes.

Apps register tasks in their AppConfig.ready() by importing their
predefined_tasks module. Each task is a callable that receives
input_data (dict) and must return a dict with the same shape as
lambda output: {"data": {...}, "routes": [...]} — routes is optional.
"""

_registry: dict[str, callable] = {}
_celery_registry: dict[str, str] = {}  # task_name -> celery task name
#: Tasks that only the platform's own dispatchers may start (2026-10-02).
_dispatch_only: set[str] = set()


def register(name: str, dispatch_only: bool = False):
    """Register a predefined task under ``name``.

    ``dispatch_only=True`` marks a node whose run is created by its app's own
    dispatcher, after that app checked who may ask and claimed the record the
    run will act on — a forum cleanup, an antivirus scan, a vault refresh or
    transfer. Such a node trusts its input to name records it may act on, so
    a person must never be able to start it by hand with input of their own
    choosing: ``api_run_list`` refuses any workflow containing one (see
    :func:`dispatch_only_tasks`), staff included. Listing and viewing those
    workflows and their runs is unaffected.
    """
    def decorator(fn):
        _registry[name] = fn
        if dispatch_only:
            _dispatch_only.add(name)
        else:
            _dispatch_only.discard(name)
        return fn
    return decorator


def is_dispatch_only(name: str) -> bool:
    return name in _dispatch_only


def dispatch_only_tasks(workflow) -> list[str]:
    """The dispatch-only task names among this workflow's nodes, sorted.

    Empty means a person may start the workflow by hand (subject to the
    usual checks); anything else means only the owning app's dispatcher may.
    """
    from .models import WorkflowNode

    names = workflow.nodes.filter(
        node_type=WorkflowNode.PREDEFINED_TASK,
    ).values_list("task_name", flat=True)
    return sorted({name for name in names if name in _dispatch_only})


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
