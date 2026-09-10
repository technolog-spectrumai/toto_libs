"""Mounting Capsules and running executions inside them.

**The manager keeps no database.** Everything it needs to answer a question is
derivable from three durable-enough places:

* Docker labels — which container belongs to which Capsule and execution;
* the cgroup tree — what a Capsule is using right now;
* the staging directory layout — where an execution's input and output live.

That is not an optimisation, it is the invariant: "destroy every runner, every
scratch area and the manager itself" has to be survivable, and a manager with
its own database would make it a data loss. A restarted manager rebuilds its
whole view by looking, which also means it cannot drift from reality.

Timeouts are enforced the same way — as a ``anastasia.deadline`` label read by
the reconcile loop — rather than by a thread per execution, so a manager
restart does not orphan a runner that would then run forever.

Django-free.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
import uuid as uuid_module

from ..families import family as family_for
from ..families import operation as operation_for
from ..limits import Limits
from . import images, runners, slices, staging
from .drivers import LABEL_CAPSULE, LABEL_EXEC, docker as docker_driver

log = logging.getLogger("toto.anastasia.executor.capsules")

#: Where staged input and collected output live. A volume in the compose
#: stack, and — per anastasia.md — an area that may be destroyed wholesale.
DEFAULT_STAGING_ROOT = "/var/lib/anastasia/staging"

#: How much a caller may stage in, and how much may come back. Generous enough
#: for a bucket of LaTeX sources or a video, bounded so neither direction can
#: fill the manager's volume.
DEFAULT_INPUT_BUDGET = 256 * 1024 * 1024
DEFAULT_OUTPUT_BUDGET = 256 * 1024 * 1024

LABEL_DEADLINE = "anastasia.deadline"
LABEL_OPERATION = "anastasia.operation"


class CapsuleError(Exception):
    """Something the manager cannot do, phrased for the caller's log."""


class CapsuleManager:
    def __init__(self, *, staging_root: str = DEFAULT_STAGING_ROOT,
                 slice_driver=None, docker=None, generation: str = "",
                 kernel_network: str = ""):
        self.staging_root = staging_root
        self.slices = slice_driver if slice_driver is not None else slices.detect_driver()
        self.docker = docker if docker is not None else docker_driver.DockerClient()
        # TELL THE DRIVER WHOSE RUNNERS THESE ARE.
        #
        # `anastasia.managed=1` says "an anastasia runner"; it does not say
        # WHICH executor's. Without an owner, `list_managed()` returns every
        # anastasia container on the daemon and reconcile destroys the ones it
        # cannot account for — which on 2026-09-10 meant a live executor
        # deleting the integration suite's containers mid-test, and would mean
        # two deployments on one daemon destroying each other's running jobs.
        #
        # Set here rather than at construction because the staging root is the
        # manager's fact, and `build_driver()` does not know it. Never
        # overwritten: a caller that passed an explicit owner meant it.
        if not getattr(self.docker, "owner", ""):
            try:
                self.docker.owner = self.staging_root
            except AttributeError:      # a duck-typed fake with __slots__
                pass
        #: Minted per process. It tells a caller "the manager you mounted
        #: against is not the one answering now", which is the only way a Capsule
        #: row can know it needs re-adopting after a manager restart.
        self.generation = generation or uuid_module.uuid4().hex[:16]
        #: The ONE network any runner may join, and only the kernel family
        #: does. Empty where the operator configured none, and that must mean
        #: "no network" rather than a fallback to something else.
        self.kernel_network = kernel_network

    # -- paths -------------------------------------------------------------

    #: The one place the on-disk segment is written. `reconcile.py` repeated
    #: the literal until 2026-09-10, so renaming it here left the sweeper
    #: looking in a directory nothing writes to — it reported every capsule's
    #: staging as abandoned. A path spelled in two files is a path that will
    #: disagree with itself.
    CAPSULES_DIRNAME = "capsules"

    @property
    def capsules_root(self) -> str:
        return os.path.join(self.staging_root, self.CAPSULES_DIRNAME)

    def capsule_dir(self, capsule) -> str:
        return os.path.join(self.capsules_root, str(capsule))

    def exec_dir(self, capsule, execution) -> str:
        return os.path.join(self.capsule_dir(capsule), "exec", str(execution))

    # -- mounting ----------------------------------------------------------

    def mount(self, capsule, limits: Limits) -> dict:
        """Bring a Capsule up: its cgroup ceiling and its staging area.

        A slice that cannot be created is DEGRADED, not fatal: per-runner
        limits still apply and the booking arithmetic still bounds the sum, so
        the Capsule works — it just has no hard backstop, and says so.
        """
        os.makedirs(self.capsule_dir(capsule), exist_ok=True)

        enforced = False
        detail = ""
        try:
            self.slices.ensure(capsule, limits)
            enforced = self.slices.enforced
        except Exception as exc:  # noqa: BLE001 - reported, never fatal
            log.warning("anastasia: no cgroup ceiling for capsule %s: %s", capsule, exc)
            detail = ("This host would not create a cgroup for the Capsule, so its "
                      "combined ceiling is bookkeeping only. Each job is still "
                      "limited individually.")

        return {
            "manager_generation": self.generation,
            "slice_enforced": enforced,
            "slice_driver": self.slices.name,
            # Which isolation this Capsule was actually mounted under. Read off
            # the live driver, never off a setting: a setting is what somebody
            # asked for, and this is what the next job will get.
            "tier": self.docker.name,
            "detail": detail,
        }

    def unmount(self, capsule) -> dict:
        """Destroy every runner, the cgroup and the staging area.

        Order matters: containers first (so the cgroup can actually be
        released), then the slice, then the files. Every step tolerates its
        subject already being gone, because reconciliation calls this
        speculatively and a half-unmounted Capsule must be finishable.
        """
        destroyed = 0
        for row in self.docker.list_managed(capsule=str(capsule)):
            self.docker.remove(row["id"])
            destroyed += 1

        try:
            self.slices.destroy(capsule)
        except Exception:  # noqa: BLE001
            log.exception("anastasia: could not stop the slice for %s", capsule)

        shutil.rmtree(self.capsule_dir(capsule), ignore_errors=True)
        return {"unmounted": True, "runners_destroyed": destroyed}

    def status(self, capsule) -> dict:
        rows = self.docker.list_managed(capsule=str(capsule))
        running = [r for r in rows if r["state"] == "running"]
        sample = {}
        try:
            sample = self.slices.sample(capsule)
        except Exception:  # noqa: BLE001
            log.exception("anastasia: could not sample capsule %s", capsule)
        sample["executions_running"] = len(running)
        return {
            "manager_generation": self.generation,
            "mounted": os.path.isdir(self.capsule_dir(capsule)),
            "slice_enforced": self.slices.enforced,
            "tier": self.docker.name,
            "sample": sample,
        }

    # -- executing ---------------------------------------------------------

    def start_execution(self, *, capsule, execution, operation: str, params: dict,
                        limits: Limits, timeout: int, payload: bytes | None) -> dict:
        """Stage the input, create the runner, start it, and return.

        Returns as soon as the container is running: an execution is polled,
        never waited on, so a slow job cannot hold the caller's worker inside
        this call any longer than the caller chose to wait.
        """
        op = operation_for(operation)
        # Clean HERE as well as at the HTTP boundary, and not because the HTTP
        # layer is untrusted: this is the authoritative gate, and a second
        # caller (a test, a future in-process manager) must not be able to
        # reach build_argv with parameters nobody validated. Cleaning is
        # idempotent, so doing it twice costs a dict comprehension and buys the
        # guarantee that argv is only ever built from checked values — plus the
        # defaults, which is why an operation called with {} works at all.
        params = op.clean(params)
        fam = op.family
        if not self.docker.image_exists(fam.image):
            raise CapsuleError(
                f"the {fam.image} runner image is not present on this host; "
                "build the anastasia images and try again")
        # BEFORE staging anything. A mismatch costs a refusal rather than a tar
        # unpacked into a container that is about to be destroyed — and, more
        # to the point, than a job run inside an image nobody recognises.
        try:
            images.verify(self.docker, fam.key, fam.image)
        except images.ImageMismatch as exc:
            raise CapsuleError(str(exc)) from exc

        work = self.exec_dir(capsule, execution)
        input_dir = os.path.join(work, "in")
        output_dir = os.path.join(work, "out")
        # A previous attempt with this id must not leak into this one.
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)
        # The runner runs as nobody and has to be able to write /out.
        os.chmod(output_dir, 0o777)

        if payload:
            try:
                staging.unpack(payload, input_dir,
                               max_bytes=DEFAULT_INPUT_BUDGET)
            except staging.StagingError:
                shutil.rmtree(work, ignore_errors=True)
                raise

        deadline = int(time.time()) + int(timeout)
        labels = {
            LABEL_CAPSULE: str(capsule),
            LABEL_EXEC: str(execution),
            LABEL_OPERATION: op.name,
            LABEL_DEADLINE: str(deadline),
        }
        # TWO postures now, and the ordering trap that used to live here is
        # gone with the third. Assembled from the family's own declaration —
        # never from anything the caller sent, which is why `build_run_args`
        # takes a network rather than deciding one.
        #
        # The egress branch stood FIRST and shadowed this one, so the
        # python-connected family — which declared both — never got the kernel
        # network at all and reached its ZMQ ports over the egress network
        # instead. Both egress families are deleted (2026-09-10), so there is
        # one link left: the Capsule's internal network, for the kernel.
        network = self.kernel_network or None if fam.kernel_link else None
        try:
            parent = self.slices.cgroup_parent(capsule)
        except Exception:  # noqa: BLE001
            parent = None

        container = self.docker.create(
            family=fam, limits=limits, name=f"anastasia-{execution}",
            cgroup_parent=parent, input_dir=input_dir, output_dir=output_dir,
            env={"ANASTASIA_OPERATION": op.name}, labels=labels,
            argv=runners.build_argv(op, params), network=network)
        self.docker.start(container)

        return {"container": container, "deadline": deadline}

    def execution_status(self, *, capsule, execution) -> dict:
        """What an execution is doing, read from Docker rather than remembered."""
        rows = [r for r in self.docker.list_managed(capsule=str(capsule))
                if r["execution"] == str(execution)]
        if not rows:
            return {"found": False}

        row = rows[0]
        if row["state"] == "running":
            return {"found": True, "running": True}

        state = self.docker.exit_state(row["id"])
        return {
            "found": True,
            "running": False,
            "exit_code": state["exit_code"],
            "oom_killed": state["oom_killed"],
            # Whether the False above can be believed. A VM tier cannot see
            # into its own guest, so the app must not read "not an OOM" from
            # a driver that would be unable to tell. Reported as a fact about
            # the RUNTIME rather than as a tier name, so the app never has to
            # know what "kata" means.
            "oom_observable": bool(
                getattr(self.docker, "observes_guest_oom", True)),
            "logs": self.docker.logs(row["id"]),
        }

    def collect(self, *, capsule, execution) -> bytes:
        """The output tar. Bounded, and symlinks a runner left are skipped."""
        output_dir = os.path.join(self.exec_dir(capsule, execution), "out")
        if not os.path.isdir(output_dir):
            raise CapsuleError("this execution has no output directory")
        return staging.pack(output_dir, max_bytes=DEFAULT_OUTPUT_BUDGET)

    def finish_execution(self, *, capsule, execution) -> dict:
        """Destroy the runner and its scratch. Idempotent."""
        removed = 0
        for row in self.docker.list_managed(capsule=str(capsule)):
            if row["execution"] == str(execution):
                self.docker.remove(row["id"])
                removed += 1
        shutil.rmtree(self.exec_dir(capsule, execution), ignore_errors=True)
        return {"removed": removed}

    def kill_execution(self, *, capsule, execution) -> dict:
        killed = 0
        for row in self.docker.list_managed(capsule=str(capsule)):
            if row["execution"] == str(execution) and row["state"] == "running":
                self.docker.kill(row["id"])
                killed += 1
        return {"killed": killed}
