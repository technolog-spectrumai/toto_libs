"""The Gear ceiling: one cgroup per Gear, holding every runner inside it.

Layering, and why there are three drivers rather than one:

* **Per-runner limits are always applied** — ``--memory``, ``--cpus``,
  ``--pids-limit`` on every container, on every host, whatever else is
  available. One runner can never exceed its own execution limits.
* **The booking arithmetic** (``services.gear_available``) is what stops the
  SUM of a Gear's runners exceeding what the user reserved.
* **The slice** is the backstop that makes that sum *hard* rather than
  bookkeeping — a runner that somehow escaped its own limit still cannot
  escape its Gear's.

A host that cannot give us the third layer is degraded, not broken, and must
say so rather than pretend: ``describe()["enforced"]`` is False and the Gear
page shows it. That is the platform's standing rule about probes — an
unenforceable ceiling reported as enforced is worse than no ceiling.

Django-free.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time

from ..limits import Limits

log = logging.getLogger("toto.anastasia.manager.slices")

#: Where the pool ceiling lives. Provisioned once by the deploy, not by us: it
#: is a property of the machine, and a manager that could raise its own
#: aggregate ceiling would not be a ceiling.
POOL_SLICE = "anastasia.slice"

CGROUP_ROOT = "/sys/fs/cgroup"
DBUS_SOCKET = "/run/dbus/system_bus_socket"


def slice_name(gear_uuid) -> str:
    """systemd nests by DASH, so this name IS its placement under the pool."""
    hexid = getattr(gear_uuid, "hex", None) or str(gear_uuid).replace("-", "")
    return f"anastasia-gear-{hexid}.slice"


def slice_cgroup_path(unit: str) -> str:
    """The cgroup directory systemd will give a slice unit.

    systemd expands EVERY dash into a level, so ``anastasia-gear-abc.slice``
    does not live at ``anastasia.slice/anastasia-gear-abc.slice`` — it lives at
    ``anastasia.slice/anastasia-gear.slice/anastasia-gear-abc.slice``, with an
    intermediate slice systemd creates on the way. Guessing the shallow path
    finds nothing, reads no usage, and reports a healthy Gear as unmeasurable.

    Verified against ``systemctl show -p ControlGroup`` on a real transient
    slice; that property is the authority if this ever disagrees.
    """
    stem = unit[:-len(".slice")] if unit.endswith(".slice") else unit
    parts = stem.split("-")
    levels = ["-".join(parts[:i + 1]) + ".slice" for i in range(len(parts))]
    return os.path.join(CGROUP_ROOT, *levels)


def _read_int(path: str):
    """A cgroup number, or None when it is absent or says "no limit"."""
    try:
        with open(path) as handle:
            raw = handle.read().strip()
    except OSError:
        return None
    if raw in ("", "max"):
        return None
    try:
        return int(raw.split()[0])
    except ValueError:
        return None


class SliceNotSettled(Exception):
    """systemd accepted the unit but the cgroup never carried its limits."""


class SliceDriver:
    """Backends must be stateless — no per-gear state on ``self``."""

    name = "abstract"
    enforced = False

    def ensure(self, gear_uuid, limits: Limits) -> None:
        raise NotImplementedError

    def destroy(self, gear_uuid) -> None:
        raise NotImplementedError

    def exists(self, gear_uuid) -> bool:
        raise NotImplementedError

    def cgroup_parent(self, gear_uuid) -> str | None:
        """What to pass Docker as ``--cgroup-parent``, or None."""
        return None

    def sample(self, gear_uuid) -> dict:
        return {}

    def describe(self) -> dict:
        return {"driver": self.name, "enforced": self.enforced}


class SystemdSliceDriver(SliceDriver):
    """Ask systemd for a transient slice, through ``busctl``.

    ``busctl`` rather than a D-Bus library because the suite pins no such
    dependency and the manager image should stay small; ``busctl`` ships with
    systemd itself.

    A transient *slice* is used rather than a scope because a scope needs a
    process to hold it open, and an idle mounted Gear has none — that is the
    whole point of separating mounting from executing. A slice with no children
    stays active until it is stopped, so the ceiling exists before the first
    runner and survives between jobs.
    """

    name = "systemd"
    enforced = True

    #: Name of the throwaway unit the availability probe creates.
    PROBE_UNIT = "anastasia-probe.slice"

    @classmethod
    def available(cls) -> bool:
        """PROVE we can create a slice; do not merely infer it.

        ``busctl`` on PATH and a bus socket on disk say nothing about whether
        polkit will allow StartTransientUnit — an unprivileged process is
        refused with "Access denied" at the moment it matters, which is long
        after this driver has told everyone the Gear ceiling is enforced.
        A manager that believes it is enforcing when it is not is worse than
        one that knows it is not, so the probe actually creates and stops a
        unit and reports what happened.
        """
        if not shutil.which("busctl") or not os.path.exists(DBUS_SOCKET):
            return False
        driver = cls()
        try:
            probe = driver._busctl(
                "call", "org.freedesktop.systemd1", "/org/freedesktop/systemd1",
                "org.freedesktop.systemd1.Manager", "StartTransientUnit",
                "ssa(sv)a(sa(sv))", cls.PROBE_UNIT, "replace", "0", "0",
                check=False, timeout=cls.PROBE_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            log.warning("anastasia: the system bus did not answer the slice "
                        "probe in %ss; the Gear ceiling falls back",
                        cls.PROBE_TIMEOUT)
            return False
        if probe.returncode != 0:
            log.warning("anastasia: systemd will not create slices for this "
                        "process (%s); the Gear ceiling falls back",
                        (probe.stderr or "").strip()[:120])
            return False
        driver._busctl(
            "call", "org.freedesktop.systemd1", "/org/freedesktop/systemd1",
            "org.freedesktop.systemd1.Manager", "StopUnit", "ss",
            cls.PROBE_UNIT, "replace", check=False)
        return True

    #: The availability probe runs BEFORE the manager binds its port, so it
    #: must be quick. busctl's own default D-Bus reply timeout is 25s, which
    #: turned an unresponsive polkit into half a minute of apparent hang with
    #: nothing in the log yet — so the limit is passed to busctl (which is what
    #: actually bounds the CALL) as well as to the subprocess (which bounds the
    #: process if busctl itself wedges).
    PROBE_TIMEOUT = 5

    def _busctl(self, *args: str, check: bool = True,
                timeout: int = 30) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["busctl", "--system", f"--timeout={timeout}", *args],
            capture_output=True, text=True, timeout=timeout + 2, check=check)

    def ensure(self, gear_uuid, limits: Limits) -> None:
        unit = slice_name(gear_uuid)
        if self.exists(gear_uuid):
            return
        # CPUQuotaPerSecUSec is microseconds of CPU per second of wall clock:
        # 1000 millicores == one whole core == 1_000_000 us/s.
        cpu_usec = max(1000, limits.cpu_millicores * 1000)
        self._busctl(
            "call", "org.freedesktop.systemd1", "/org/freedesktop/systemd1",
            "org.freedesktop.systemd1.Manager", "StartTransientUnit",
            "ssa(sv)a(sa(sv))", unit, "replace",
            "3",
            "MemoryMax", "t", str(limits.memory_bytes),
            "TasksMax", "t", str(limits.pids),
            "CPUQuotaPerSecUSec", "t", str(cpu_usec),
            "0",
        )
        # StartTransientUnit is ASYNCHRONOUS — it returns a job object, not a
        # finished unit. Returning here would let a runner be launched into a
        # slice whose limits systemd has not written yet, which is a ceiling
        # that silently is not one. So wait for the cgroup to actually carry
        # the number we asked for.
        self._await_limits(gear_uuid, limits)

    #: How long to wait for systemd to write the limits it accepted. Generous
    #: for a loaded machine, bounded so a mount cannot hang on a wedged systemd.
    SETTLE_SECONDS = 5.0

    def _await_limits(self, gear_uuid, limits: Limits) -> None:
        path = self.path(gear_uuid)
        memory_file = os.path.join(path, "memory.max")
        deadline = time.monotonic() + self.SETTLE_SECONDS
        while time.monotonic() < deadline:
            if _read_int(memory_file) == limits.memory_bytes:
                return
            time.sleep(0.05)
        # Not fatal: per-runner limits still apply and the booking arithmetic
        # still bounds the sum. But the caller must be told the backstop is not
        # in place rather than being allowed to assume it.
        raise SliceNotSettled(
            f"{slice_name(gear_uuid)} did not take its limits within "
            f"{self.SETTLE_SECONDS:.0f}s (memory.max is "
            f"{_read_int(memory_file)!r}, wanted {limits.memory_bytes})")

    def destroy(self, gear_uuid) -> None:
        """Stop the slice, which kills everything still inside it.

        That IS the teardown: a Gear's runners do not need to be enumerated and
        killed one by one, because stopping their cgroup parent takes them all.
        A slice that is already gone is not an error — teardown is idempotent by
        contract, and reconciliation calls it speculatively.
        """
        result = self._busctl(
            "call", "org.freedesktop.systemd1", "/org/freedesktop/systemd1",
            "org.freedesktop.systemd1.Manager", "StopUnit", "ss",
            slice_name(gear_uuid), "replace", check=False)
        if result.returncode != 0 and "not loaded" not in (result.stderr or ""):
            log.warning("anastasia: could not stop %s: %s",
                        slice_name(gear_uuid), (result.stderr or "").strip())

    def exists(self, gear_uuid) -> bool:
        return os.path.isdir(self.path(gear_uuid))

    def path(self, gear_uuid) -> str:
        return slice_cgroup_path(slice_name(gear_uuid))

    def cgroup_parent(self, gear_uuid) -> str:
        return slice_name(gear_uuid)

    def sample(self, gear_uuid) -> dict:
        return read_cgroup(self.path(gear_uuid))


class CgroupfsSliceDriver(SliceDriver):
    """Write the cgroup directly. For hosts where systemd is not reachable.

    Honest about the caveat: cgroup v2 has a single-writer rule, and a
    directory we mkdir under a systemd-managed slice is a second writer.
    It works, and systemd will not fight us over a subtree it did not create,
    but it is the fallback rather than the default for exactly that reason.
    """

    name = "cgroupfs"
    enforced = True

    @staticmethod
    def available() -> bool:
        return os.access(os.path.join(CGROUP_ROOT, "cgroup.procs"), os.W_OK)

    def path(self, gear_uuid) -> str:
        return slice_cgroup_path(slice_name(gear_uuid))

    def ensure(self, gear_uuid, limits: Limits) -> None:
        parent = os.path.dirname(self.path(gear_uuid))
        os.makedirs(parent, exist_ok=True)
        # Controllers must be delegated by the PARENT before a child can use
        # them; without this the child's memory.max simply does not exist.
        self._enable_controllers(parent)
        target = self.path(gear_uuid)
        os.makedirs(target, exist_ok=True)
        self._write(os.path.join(target, "memory.max"), str(limits.memory_bytes))
        self._write(os.path.join(target, "pids.max"), str(limits.pids))
        self._write(os.path.join(target, "cpu.max"),
                    f"{max(1000, limits.cpu_millicores * 100)} 100000")

    @staticmethod
    def _enable_controllers(parent: str) -> None:
        try:
            with open(os.path.join(parent, "cgroup.subtree_control"), "w") as fh:
                fh.write("+memory +pids +cpu")
        except OSError:
            log.debug("anastasia: could not delegate controllers in %s", parent)

    @staticmethod
    def _write(path: str, value: str) -> None:
        try:
            with open(path, "w") as handle:
                handle.write(value)
        except OSError as exc:
            log.warning("anastasia: could not set %s=%s (%s)", path, value, exc)

    def destroy(self, gear_uuid) -> None:
        target = self.path(gear_uuid)
        if not os.path.isdir(target):
            return
        try:
            os.rmdir(target)
        except OSError as exc:
            # A cgroup with processes still in it refuses to go. The caller
            # kills the containers first; this is the tidy-up behind them.
            log.warning("anastasia: cgroup %s not empty yet (%s)", target, exc)

    def exists(self, gear_uuid) -> bool:
        return os.path.isdir(self.path(gear_uuid))

    def cgroup_parent(self, gear_uuid) -> str:
        return os.path.relpath(self.path(gear_uuid), CGROUP_ROOT)

    def sample(self, gear_uuid) -> dict:
        return read_cgroup(self.path(gear_uuid))


class NullSliceDriver(SliceDriver):
    """No aggregate ceiling available. Per-runner limits still apply.

    Reports ``enforced: False`` so the Gear page can say the backstop is
    missing instead of implying a guarantee this host cannot make.
    """

    name = "null"
    enforced = False

    def ensure(self, gear_uuid, limits):  # noqa: D102 - nothing to do
        return None

    def destroy(self, gear_uuid):
        return None

    def exists(self, gear_uuid) -> bool:
        return False

    def sample(self, gear_uuid) -> dict:
        return {}


def read_cgroup(path: str) -> dict:
    """Live usage from a cgroup v2 directory. Empty when it is not there.

    The same files ``toto.monit.collectors`` reads, one level down: this asks
    about ONE Gear rather than about the whole container.
    """
    if not os.path.isdir(path):
        return {}

    sample: dict = {}
    current = _read_int(os.path.join(path, "memory.current"))
    if current is not None:
        sample["ram_mb_used"] = current // (1024 * 1024)
    peak = _read_int(os.path.join(path, "memory.peak"))
    if peak is not None:
        sample["ram_mb_peak"] = peak // (1024 * 1024)
    pids = _read_int(os.path.join(path, "pids.current"))
    if pids is not None:
        sample["pids_used"] = pids

    # cpu.stat is key/value lines; usage_usec is cumulative since the cgroup
    # was created, so a rate needs two readings — the caller keeps the
    # previous one. Reporting the raw counter keeps this function pure.
    try:
        with open(os.path.join(path, "cpu.stat")) as handle:
            for line in handle:
                key, _, value = line.partition(" ")
                if key == "usage_usec":
                    sample["cpu_usage_usec"] = int(value.strip())
                    break
    except (OSError, ValueError):
        pass

    # memory.events counts oom_kill cumulatively. It is the ONLY reliable way
    # to learn that something in this Gear was killed for memory: the container
    # is gone by the time anyone asks, and its exit code (137) is
    # indistinguishable from an ordinary SIGKILL.
    try:
        with open(os.path.join(path, "memory.events")) as handle:
            for line in handle:
                key, _, value = line.partition(" ")
                if key == "oom_kill":
                    sample["oom_kills"] = int(value.strip())
                    break
    except (OSError, ValueError):
        pass

    return sample


def detect_driver() -> SliceDriver:
    """Pick the best ceiling this host can actually give us."""
    if SystemdSliceDriver.available():
        return SystemdSliceDriver()
    if CgroupfsSliceDriver.available():
        return CgroupfsSliceDriver()
    log.warning(
        "anastasia: no cgroup driver available — per-runner limits still "
        "apply, but a Gear's aggregate ceiling is bookkeeping only")
    return NullSliceDriver()
