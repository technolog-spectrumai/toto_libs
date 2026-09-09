"""Shared fixture: a pool, a user, and a Gear that fits in it."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from toto.anastasia.limits import Limits

#: Deliberately small and exactly divisible, so a test can say "reserve half"
#: without arithmetic in the assertion.
POOL = {"cpu_millicores": 4000, "ram_mb": 8192, "scratch_mb": 8192, "pids": 1024}

HALF = Limits(cpu_millicores=2000, ram_mb=4096, scratch_mb=4096, pids=512)
SMALL = Limits(cpu_millicores=500, ram_mb=1024, scratch_mb=1024, pids=128)
#: Big enough for one runner at the family defaults — what a test that
#: actually submits work needs. SMALL is deliberately below them, so it is
#: also the fixture for "this job will never fit in this Gear".
RUNNABLE = Limits(cpu_millicores=1500, ram_mb=2048, scratch_mb=2048, pids=256)


@override_settings(
    ANASTASIA_POOL=POOL,
    ANASTASIA_MAX_GEARS_PER_USER=3,
    ANASTASIA_RUNTIME_BACKEND="toto.anastasia.tests.base.FakeRuntimeBackend",
)
class AnastasiaTestCase(TestCase):
    """Every anastasia test runs against a configured pool and a fake runtime."""

    def setUp(self):
        super().setUp()
        FakeRuntimeBackend.reset()
        # A scratch DB needs a Platform row: index() goes through
        # PageProcessor, whose _get_config raises Http404 when no active
        # platform exists — so without this every page test dies with a 404
        # whose context has none of the view's keys, and the KeyError it
        # produces ("gears") points nowhere near the cause. Same fixture the
        # ocr tests carry, for the same reason.
        from toto.core.models import Platform

        Platform.objects.create(site_name="Test", author="Test",
                                publication_year=2024, active=True)
        User = get_user_model()
        self.user = User.objects.create_user("gearowner", password="x")
        self.other = User.objects.create_user("someoneelse", password="x")


class FakeRuntimeBackend:
    """A runtime that records instead of containing.

    Class-level state rather than instance state because ``get_backend()``
    constructs a fresh backend per call — the ABC's "backends must be
    stateless" rule — so a test could never reach the instance it wants.
    """

    name = "fake"

    calls: list = []
    fail_mount = False
    fail_start = False
    generation = "gen-test"

    @classmethod
    def reset(cls):
        cls.calls = []
        cls.fail_mount = False
        cls.fail_start = False

    def mount(self, lease):
        type(self).calls.append(("mount", str(lease.uuid)))
        if type(self).fail_mount:
            from toto.anastasia.runtime import RuntimeUnavailable
            raise RuntimeUnavailable("the fake manager is down")
        return {"manager_generation": type(self).generation}

    def unmount(self, lease):
        type(self).calls.append(("unmount", str(lease.uuid)))
        return {"unmounted": True, "runners_destroyed": 2}

    def status(self, lease):
        return {}

    def start_execution(self, execution, *, params, payload):
        type(self).calls.append(("start", str(execution.uuid), params))
        if type(self).fail_start:
            from toto.anastasia.runtime import RuntimeUnavailable
            raise RuntimeUnavailable("no runner could be created")
        return {"started": True}

    def kill_execution(self, execution):
        type(self).calls.append(("kill", str(execution.uuid)))
        return {"killed": True}

    def describe(self):
        return {"backend": "fake", "name": "fake"}
