"""A Docker that records instead of containing, and a slice driver that counts.

Lets the whole manager be exercised — mount, execute, collect, reconcile,
unmount — on a machine with no Docker and no privileges, which is what the
gate runs on.
"""

from __future__ import annotations

import itertools

from toto.anastasia.executor import drivers
from toto.anastasia.executor.drivers import docker as containers


class FakeDocker:
    """Enough of :class:`DockerClient` for the manager to be driven.

    It reports a tier of its own rather than borrowing "docker": a test that
    asserted the tier and got the real name would pass whether or not the
    value came from the driver at all, which is the one thing the tier is
    there to prove.
    """

    #: Part of the Driver contract. `gears.mount()` reads it, so a fake without
    #: one makes every mount an AttributeError.
    name = "fake"

    def __init__(self, *, images=("anastasia-pdf", "anastasia-latex",
                                  "anastasia-media", "anastasia-ocr",
                                  "anastasia-python")):
        self.images = set(images)
        self.containers: dict[str, dict] = {}
        self.removed: list[str] = []
        self.killed: list[str] = []
        self.created_args: list[list] = []
        self._ids = (f"fake{n:04d}" for n in itertools.count(1))
        self.fail_create = False

    # -- the surface the manager uses -------------------------------------

    def available(self):
        return True

    def image_exists(self, image):
        return image in self.images

    def create(self, **kwargs):
        # Go through the REAL argument builder so the fake cannot accidentally
        # accept a call the true client would refuse (a forbidden env var, for
        # instance) — the fake must not be more permissive than the thing it
        # stands in for.
        args = containers.DockerClient().build_run_args(**kwargs)
        self.created_args.append(args)
        if self.fail_create:
            raise containers.DockerError("fake docker refused to create")
        cid = next(self._ids)
        labels = dict(kwargs.get("labels") or {})
        labels[drivers.LABEL_MANAGED] = "1"
        self.containers[cid] = {
            "id": cid, "name": kwargs.get("name", ""), "state": "created",
            "labels": labels, "exit_code": 0, "oom_killed": False,
            "logs": "", "argv": kwargs.get("argv"),
        }
        return cid

    def start(self, cid):
        self.containers[cid]["state"] = "running"

    def wait(self, cid, timeout):
        self.containers[cid]["state"] = "exited"
        return {"timed_out": False}

    def inspect(self, cid):
        row = self.containers.get(cid)
        if row is None:
            return {}
        return {"State": {"ExitCode": row["exit_code"],
                          "OOMKilled": row["oom_killed"], "Error": ""},
                "Config": {"Labels": row["labels"]}}

    def labels(self, cid):
        """Part of the driver contract since 2026-09-10, so the fake owes it
        too — a fake that is missing a method the real driver has makes the
        suite pass over a seam nothing implements."""
        return (self.containers.get(cid) or {}).get("labels") or {}

    def exit_state(self, cid):
        row = self.containers.get(cid) or {}
        return {"exit_code": row.get("exit_code"),
                "oom_killed": row.get("oom_killed", False),
                "error": "", "started_at": None, "finished_at": None}

    def logs(self, cid, tail=200):
        return (self.containers.get(cid) or {}).get("logs", "")

    def kill(self, cid):
        self.killed.append(cid)
        if cid in self.containers:
            self.containers[cid]["state"] = "exited"

    def remove(self, cid):
        self.removed.append(cid)
        self.containers.pop(cid, None)

    def list_managed(self, *, gear=None):
        out = []
        for row in self.containers.values():
            labels = row["labels"]
            found = (labels.get(drivers.LABEL_CAPSULE)
                     or labels.get(drivers.LABEL_CAPSULE_LEGACY, ""))
            if gear is not None and found != gear:
                continue
            out.append({
                "id": row["id"], "name": row["name"], "state": row["state"],
                # Mirrors the real driver: a container labelled before the
                # rename is still owned, not orphaned.
                "gear": found,
                "execution": labels.get(drivers.LABEL_EXEC, ""),
            })
        return out

    # -- test conveniences -------------------------------------------------

    def finish(self, cid, *, exit_code=0, oom_killed=False, logs=""):
        row = self.containers[cid]
        row.update(state="exited", exit_code=exit_code,
                   oom_killed=oom_killed, logs=logs)


class CountingSliceDriver:
    """A slice driver that only remembers what it was asked to do."""

    name = "counting"
    enforced = True

    def __init__(self, *, fail_ensure=False):
        self.ensured: dict = {}
        self.destroyed: list = []
        self.fail_ensure = fail_ensure

    def ensure(self, gear, limits):
        if self.fail_ensure:
            raise RuntimeError("this host will not create cgroups")
        self.ensured[str(gear)] = limits

    def destroy(self, gear):
        self.destroyed.append(str(gear))
        self.ensured.pop(str(gear), None)

    def exists(self, gear):
        return str(gear) in self.ensured

    def cgroup_parent(self, gear):
        return f"anastasia-gear-{gear}.slice"

    def sample(self, gear):
        return {"ram_mb_used": 12, "pids_used": 3, "oom_kills": 0}

    def describe(self):
        return {"driver": self.name, "enforced": self.enforced}
