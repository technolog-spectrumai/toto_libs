"""The module Celery's autodiscovery actually looks for.

``autodiscover_tasks`` imports ``<package>.tasks`` and nothing else, so a task
defined only in ``tasks_direct`` is registered in whichever process *imports*
that module — the web worker, via ``views.py`` — and never in the Celery worker
that has to run it. The producer therefore enqueues
``toto.manta.tasks.run_direct`` happily and the consumer rejects it as
unregistered: the job sits in the queue and nothing reports why.

The task's own ``name=`` already said ``toto.manta.tasks.run_direct``, which is
where that expectation was written down; this module is what makes it true. The
re-export keeps a single task object, so ``views.py``'s
``from .tasks_direct import run_direct_job`` and the tests that patch
``toto.manta.tasks_direct.run_direct_job.delay`` both still refer to this one.

A host that pins toto-media-ops must also add ``"toto.manta"`` to
``registry.TASK_MODULES``; this file is the other half of that, and neither half
works alone.
"""
from .tasks_direct import run_direct_job

__all__ = ["run_direct_job"]
