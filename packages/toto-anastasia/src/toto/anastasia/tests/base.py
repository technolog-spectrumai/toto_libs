"""Shared fixture: a pool, a user, and a Capsule that fits in it."""

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
#: also the fixture for "this job will never fit in this Capsule".
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
        # produces ("capsules") points nowhere near the cause. Same fixture the
        # ocr tests carry, for the same reason.
        from toto.core.models import Platform

        Platform.objects.create(site_name="Test", author="Test",
                                publication_year=2024, active=True)
        User = get_user_model()
        self.user = User.objects.create_user("capsuleowner", password="x")
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
    #: What a running job has printed. Set by a test that wants to read it
    #: back through `execution_logs`.
    log_text = ""
    #: Flip to make the log reader unreachable — the shape a real runtime
    #: takes when the executor is down, which must degrade rather than 500.
    fail_logs = False
    #: The files area, per capsule uuid: {uuid: {name: bytes}}. In memory, so
    #: a transfer test can put a file in one end and read it out of the other
    #: without a directory on disk — the real area's path rules are
    #: `executor/files.py`'s and are tested there against a real filesystem.
    files: dict = {}
    #: Names the fake refuses, so a test can see a refusal travel up to the
    #: API without needing a real traversal attempt.
    refuse_files: set = set()
    #: What `execution_status` answers. None means "still running"; a dict is
    #: returned as the settled state — `{"found": True, "running": False,
    #: "exit_code": 0}` is a clean finish. Set by a test that wants a watched
    #: job (an install) to end.
    settled = None

    @classmethod
    def reset(cls):
        cls.calls = []
        cls.fail_mount = False
        cls.fail_start = False
        cls.log_text = ""
        cls.fail_logs = False
        cls.files = {}
        cls.refuse_files = set()
        cls.settled = None

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

    def execution_logs(self, execution, offset=0):
        """Slice `log_text` the way the real driver slices a container's log.

        Modelled on `drivers/docker.py:logs_since` rather than invented: it
        takes a BYTE offset into the accumulated output, tolerates junk by
        starting from zero, and reports where the next slice begins. A fake
        that returned the whole log regardless of offset would let a broken
        cursor pass.
        """
        type(self).calls.append(("logs", str(execution.uuid), offset))
        if type(self).fail_logs:
            return {"text": "", "offset": offset, "complete": False,
                    "found": False}
        raw = type(self).log_text.encode("utf-8")
        try:
            offset = max(0, int(offset))
        except (TypeError, ValueError):
            offset = 0
        offset = min(offset, len(raw))
        return {"text": raw[offset:].decode("utf-8", "replace"),
                "offset": len(raw), "complete": True, "found": True}

    def execution_status(self, execution):
        """Running until a test says otherwise. Modelled on the manager's own
        answer shape (`capsules.execution_status`), because `jobs._finish`
        reads `found`, `running`, `exit_code` and `oom_killed` off it."""
        type(self).calls.append(("status", str(execution.uuid)))
        if type(self).settled is None:
            return {"found": True, "running": True}
        return dict(type(self).settled)

    def collect(self, execution):
        """An empty output tar. The install path collects like any job and a
        fake that returned junk would make `_collect` log a warning per poll."""
        from toto.anastasia.jobs import tar_of

        type(self).calls.append(("collect", str(execution.uuid)))
        return tar_of({})

    def finish_execution(self, execution):
        """Destroy the runner — AND ITS LOG WITH IT.

        Modelled on the manager, not invented: `capsules.execution_logs`
        finds the container by label and answers `{"text": "", "found":
        False}` when there is none, which is exactly the state a swept job is
        in. A fake that kept answering with the full log after destruction
        would let a reader that pulls its log too late pass — and the tail of
        an install's log is the part its count is derived from.
        """
        type(self).calls.append(("finish", str(execution.uuid)))
        type(self).log_text = ""
        type(self).fail_logs = True
        return {"finished": True}

    # -- the files area -----------------------------------------------------
    #
    # The same four verbs the real backend has, over a dict. What is NOT
    # modelled: the path rules — `safe_name`, the fd walk, links. Those live in
    # `executor/files.py` and are tested against a real filesystem in
    # test_files; a fake that re-implemented them would be a second rule.

    def _area(self, lease):
        return type(self).files.setdefault(str(lease.uuid), {})

    def _refuse(self, name):
        from toto.anastasia.executor_backend import FilesRefused

        if name in type(self).refuse_files:
            raise FilesRefused(f"{name!r} is refused by the fake")

    def capsule_files(self, lease):
        type(self).calls.append(("files", str(lease.uuid)))
        area = self._area(lease)
        return {"files": [{"name": n, "size": len(b), "type": "file"}
                          for n, b in sorted(area.items())],
                "complete": True}

    def capsule_file_read(self, lease, name):
        from toto.anastasia.executor_backend import FilesRefused

        type(self).calls.append(("file_read", str(lease.uuid), name))
        self._refuse(name)
        area = self._area(lease)
        if name not in area:
            raise FilesRefused(f"there is no file named {name!r}")
        return area[name]

    def capsule_file_write(self, lease, name, data, replace=False):
        from toto.anastasia.executor_backend import FilesRefused

        type(self).calls.append(("file_write", str(lease.uuid), name, replace))
        self._refuse(name)
        area = self._area(lease)
        if name in area and not replace:
            raise FilesRefused(f"{name!r} already exists")
        area[name] = bytes(data)
        return {"name": name, "bytes": len(data), "replaced": name in area}

    def capsule_file_delete(self, lease, name):
        from toto.anastasia.executor_backend import FilesRefused

        type(self).calls.append(("file_delete", str(lease.uuid), name))
        self._refuse(name)
        area = self._area(lease)
        if name not in area:
            raise FilesRefused(f"there is no file named {name!r}")
        del area[name]
        return {"name": name, "deleted": True}

    def describe(self):
        return {"backend": "fake", "name": "fake"}
