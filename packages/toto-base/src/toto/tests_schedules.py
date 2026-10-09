"""Everything beat enqueues, the worker can find.

There is one bug in this file's blast radius and it has now happened twice —
`toto.monit` and `toto.weather`. Both times the shape was identical: a task
scheduled in :func:`toto.schedules.beat_schedule` whose app label was missing
from :data:`toto.registry.TASK_MODULES`, so beat enqueued it forever and the
worker answered ``KeyError`` and discarded it. It looks like a broken worker in
the log and buries the stack traces that matter.

`toto/monit/tests.py` asserts the membership for monit specifically. This
asserts it for **every** scheduled task, so the third one is caught by the
existing suite rather than by somebody reading logs.
"""

from django.test import SimpleTestCase


def every_scheduled_task():
    """Every task name beat can emit, with every feature switched on.

    Introspects the keyword arguments rather than listing them, so a feature
    added to `beat_schedule` is covered without anyone remembering to come here.
    """
    import inspect

    from toto import schedules

    signature = inspect.signature(schedules.beat_schedule)
    enabled = {name: True for name, parameter in signature.parameters.items()
               if parameter.default is False}
    return {entry["task"] for entry in schedules.beat_schedule(**enabled).values()}


class BeatScheduleTests(SimpleTestCase):
    def test_there_is_something_to_check(self):
        """Guards the guard: an introspection bug would make this file vacuous."""
        self.assertGreater(len(every_scheduled_task()), 3)

    def test_every_scheduled_task_lives_in_a_discoverable_module(self):
        """The whole point of the file.

        `celery_app.py` calls `autodiscover_tasks(TASK_MODULES)`, and that list
        is the ONLY source — an app being in INSTALLED_APPS does not make its
        tasks discoverable. So a task's app label must appear here or the job is
        enqueued and thrown away.
        """
        from toto.registry import TASK_MODULES

        discoverable = set(TASK_MODULES)
        for task in sorted(every_scheduled_task()):
            with self.subTest(task=task):
                # "toto.weather.tasks.auto_refresh_weather" -> "toto.weather"
                label = ".".join(task.split(".")[:2])
                self.assertIn(
                    label, discoverable,
                    f"beat schedules {task} but the worker autodiscovers "
                    f"{label!r} from nowhere — it will answer KeyError and "
                    f"discard the job on every tick.")

    def test_a_scheduled_task_names_a_module_that_could_hold_it(self):
        """Autodiscovery imports ``<label>.tasks``.

        `toto.manta`'s note in registry.py records this the hard way: the entry
        was present, the task lived in `tasks_direct`, and the worker still
        never registered it. So the module the name implies has to be the one
        autodiscovery would import.
        """
        for task in sorted(every_scheduled_task()):
            with self.subTest(task=task):
                parts = task.split(".")
                self.assertGreaterEqual(len(parts), 4, task)
                self.assertEqual(
                    parts[2], "tasks",
                    f"{task} is not in <app>.tasks, which is the only module "
                    f"autodiscover_tasks imports.")

    def test_nothing_of_the_parked_forum_is_scheduled(self):
        """The forum is parked since 2026-10-09: its nightly cleanup left the
        schedule and its label left the worker's list in one edit, so the
        pairing above holds with neither half."""
        import inspect

        from toto import schedules
        from toto.registry import TASK_MODULES

        parameters = inspect.signature(schedules.beat_schedule).parameters
        self.assertFalse([name for name in parameters if "forum" in name])
        self.assertFalse([task for task in every_scheduled_task() if "forum" in task])
        self.assertNotIn("toto.forum", TASK_MODULES)
