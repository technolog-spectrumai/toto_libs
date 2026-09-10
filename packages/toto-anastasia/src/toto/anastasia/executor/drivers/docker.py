"""The module that talks to a container runtime through the Docker CLI.

Through the CLI rather than a Python SDK, for the reasons the rest of this
repository already settled: no host, no wheel and no requirements file pins a
Docker library, ``deploy.py`` shells ``docker compose``, and the executor's
venv stays small enough to reason about. Everything here goes through
:meth:`DockerClient._run`, so a test can substitute one method.

**What a runner is allowed to be is decided HERE, not by a caller.** The
argument list below is assembled from a Family and an execution's limits; there
is no parameter anywhere in the API that reaches it. That is what makes
"callers cannot choose Docker parameters" a structural fact rather than a
filter someone has to maintain.

IT SERVES MORE THAN ONE ISOLATION TIER. ``--runtime`` selects the OCI runtime
the daemon hands the container to, so a VM-backed tier (Kata) is this same
argv with one more flag rather than a second stack with its own client, its own
parsing and its own hardening table to keep in step. ``KataDriver`` below is
that subclass, and the arrangement is deliberate: every flag in
``build_run_args`` is written once, so a tier cannot quietly lose one.

Django-free.
"""

from __future__ import annotations

import json
import logging
import shlex
import subprocess
import time

from ...families import Family
from ...limits import Limits
from . import (FORBIDDEN_ENV_PREFIXES, LABEL_CAPSULE,  # noqa: F401
               LABEL_CAPSULE_LEGACY, LABEL_EXEC, LABEL_MANAGED, LABEL_OWNER,
               RUNNER_UID, Driver, DriverError)

log = logging.getLogger("toto.anastasia.executor.drivers.docker")


class DockerError(DriverError):
    """Docker said no. The message is for a log, not for a user.

    A ``DriverError`` since 2026-09-10 so ``service.py`` can catch the base and
    answer 502 without naming a runtime. Kept as its own name because the
    message it carries is Docker's.
    """


class DockerClient(Driver):
    """A narrow, opinionated wrapper. Not a general Docker binding."""

    name = "docker"

    #: The OCI runtime the daemon hands containers to. ``None`` means the
    #: daemon's default (runc), which is what ``--runtime`` being absent gets.
    runtime = None

    def __init__(self, binary: str = "docker", timeout: int = 60,
                 runtime: str | None = None, owner: str = ""):
        self.binary = binary
        self.timeout = timeout
        #: Which executor's runners these are. Set by the manager from its
        #: staging root; empty only in tests that never reconcile.
        self.owner = owner
        if runtime is not None:
            self.runtime = runtime

    # -- plumbing ---------------------------------------------------------

    def _run(self, args: list[str], *, timeout: int | None = None,
             check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(
            [self.binary, *args], capture_output=True, text=True,
            timeout=timeout or self.timeout)
        if check and result.returncode != 0:
            raise DockerError(
                f"docker {' '.join(args[:2])} failed ({result.returncode}): "
                f"{(result.stderr or '').strip()[:400]}")
        return result

    def available(self) -> bool:
        try:
            return self._run(["version", "--format", "{{.Server.Version}}"],
                             check=False).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def image_exists(self, image: str) -> bool:
        return self._run(["image", "inspect", image], check=False).returncode == 0

    def image_digest(self, image: str) -> str:
        """The image's content digest, or "" if it has none.

        `RepoDigests` is empty for an image built locally and never pushed,
        which is every runner image on a host that builds its own. That is why
        the digest check below TREATS AN ABSENT DIGEST AS UNPINNED rather than
        as a mismatch: refusing a locally-built image would break the ordinary
        deployment to guard against a supply-chain attack the deployment is not
        exposed to.
        """
        info = self.inspect(image) or {}
        digests = info.get("RepoDigests") or []
        for entry in digests:
            if "@" in entry:
                return entry.split("@", 1)[1]
        return str(info.get("Id") or "")

    # -- creating a runner ------------------------------------------------

    def build_run_args(self, *, family: Family, limits: Limits, name: str,
                       cgroup_parent: str | None, input_dir: str,
                       output_dir: str, env: dict | None,
                       labels: dict, argv: list[str],
                       network: str | None = None) -> list[str]:
        """Every flag a runner gets. Assembled, never accepted.

        Read this as the security model in one place:

        * ``--read-only`` — the image is not writable, so a runner cannot
          persist anything into it for the next execution to find.
        * ``--tmpfs /scratch`` with an explicit size — the ONLY writable place,
          and hard-bounded. The deploy hosts run overlayfs on ext4 where
          ``--storage-opt size=`` does not exist, so a tmpfs is how a scratch
          quota is made real at all. ``noexec`` because no family needs to run
          a binary it just wrote, and ``nosuid``/``nodev`` for the obvious.
        * ``--cap-drop ALL`` + ``no-new-privileges`` — nothing to escalate to.
        * ``--user`` non-root — a container escape lands as nobody.
        * ``--network none``. Since 2026-09-10 that is every runner of every
          family: the python family was the last exception and its network
          existed to reach a kernel that no longer runs. The parameter stays
          because it is how ``none`` is spelled, and because a future posture
          must arrive through a declaration rather than by growing a new flag.
        * ``--pids-limit`` — a fork bomb hits a wall instead of the host.
        * ``--cgroup-parent`` — the Capsule's ceiling, above this runner's own.
        * ``--rm=false`` — the container is removed EXPLICITLY after its exit
          state has been read. ``--rm`` would delete the evidence (exit code,
          OOMKilled) before anyone could look at it.
        """
        args = ["create"]
        # WHICH ISOLATION. Absent, the daemon uses its default (runc) and the
        # job shares this kernel; named, the daemon refuses outright if the
        # runtime is not registered — verified against Docker 29.6:
        #
        #     docker run --runtime nonexistent-probe ...
        #     → Error response from daemon: unknown or invalid runtime name
        #
        # That refusal is the property this tier rests on. A host that believes
        # it runs VMs and does not gets an error rather than a silent
        # downgrade to a shared kernel, which is the one failure mode an
        # isolation flag must never have.
        if self.runtime:
            args += ["--runtime", self.runtime]
        args += [
            "--name", name,
            "--user", RUNNER_UID,
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", str(limits.pids),
            "--memory", str(limits.memory_bytes),
            # Equal to --memory: no swap. Without this a runner over its memory
            # limit swaps instead of dying, and a "bounded" job quietly becomes
            # an unbounded one that ruins the host's IO.
            "--memory-swap", str(limits.memory_bytes),
            "--cpus", limits.docker_cpus,
            "--tmpfs",
            f"/scratch:rw,noexec,nosuid,nodev,size={limits.scratch_mb}m,mode=1777",
            "--mount", f"type=bind,source={input_dir},target=/in,readonly",
            "--mount", f"type=bind,source={output_dir},target=/out",
            "--workdir", "/scratch",
        ]
        args += ["--network", network or "none"]
        if cgroup_parent:
            args += ["--cgroup-parent", cgroup_parent]

        for key, value in sorted((labels or {}).items()):
            args += ["--label", f"{key}={value}"]
        args += ["--label", f"{LABEL_MANAGED}=1"]
        if self.owner:
            args += ["--label", f"{LABEL_OWNER}={self.owner}"]

        for key, value in sorted((env or {}).items()):
            self._refuse_forbidden_env(key)
            args += ["--env", f"{key}={value}"]

        # A tier's own compensations go HERE, while flags are still flags.
        # Appending after the image would hand them to the runner as command
        # arguments instead — which is exactly what a first attempt did, and
        # the argv test did not catch because it only asked whether the flag
        # was PRESENT, not where.
        args += self.extra_run_flags(limits)

        args.append(family.image)
        args += list(argv)
        return args

    def extra_run_flags(self, limits: Limits) -> list[str]:
        """Flags this tier needs to keep a promise the shared table cannot.

        Empty for a shared kernel: `build_run_args` above already enforces
        everything on offer. Overridden by a VM tier, where one limit does not
        survive the trip into the guest. Kept as a named seam so a difference
        between tiers has to be written down somewhere a reader will find it.
        """
        return []

    @staticmethod
    def _refuse_forbidden_env(key: str) -> None:
        """A credential must not reach a runner even by mistake.

        Raised rather than filtered: silently dropping a variable the caller
        thought it was passing produces a runner that fails for a reason nobody
        can see. This is a programming error and should read like one.
        """
        upper = key.upper()
        for prefix in FORBIDDEN_ENV_PREFIXES:
            if upper.startswith(prefix):
                raise DockerError(
                    f"refusing to pass {key!r} to a runner: a runner never "
                    "receives database, Vault or application credentials")

    def create(self, **kwargs) -> str:
        args = self.build_run_args(**kwargs)
        return self._run(args).stdout.strip()

    def start(self, container: str) -> None:
        self._run(["start", container])

    def wait(self, container: str, timeout: int) -> dict:
        """Block until the runner exits, or kill it at the deadline.

        A timeout is a RESULT, not an exception: the caller wants the same
        shape back either way, and "this ran too long" is something a user has
        to be told in a sentence rather than as a stack trace.
        """
        try:
            self._run(["wait", container], timeout=timeout)
            return {"timed_out": False}
        except subprocess.TimeoutExpired:
            log.info("anastasia: %s passed its %ss deadline", container, timeout)
            self.kill(container)
            return {"timed_out": True}

    def inspect(self, container: str) -> dict:
        result = self._run(["inspect", container], check=False)
        if result.returncode != 0:
            return {}
        try:
            return (json.loads(result.stdout) or [{}])[0]
        except (ValueError, IndexError):
            return {}

    def labels(self, container: str) -> dict:
        """The labels a sandbox carries, WITHOUT the caller parsing our JSON.

        `reconcile.py` used to reach through `inspect()` and dig
        ``Config.Labels`` out of the raw `docker inspect` document — which made
        the seam leak this runtime's wire format into a module that is supposed
        to be runtime-neutral. A second driver would have had to fabricate a
        fake Docker inspect object to satisfy it.

        Labels are how a restarted executor rebuilds its world, so this is
        contract rather than convenience: every driver must answer it.
        """
        return ((self.inspect(container) or {}).get("Config") or {}).get("Labels") or {}

    def exit_state(self, container: str) -> dict:
        """Exit code and — the part that matters — whether the kernel OOM-killed it.

        Exit code 137 is SIGKILL and says nothing about why: the timeout kill
        above produces exactly the same code. ``State.OOMKilled`` is the only
        thing that distinguishes "this job needed more memory than its Capsule
        allows" from "this job was stopped", and those need different sentences.
        """
        state = self._settled_state(container)
        return {
            "exit_code": state.get("ExitCode"),
            "oom_killed": bool(state.get("OOMKilled")),
            "error": (state.get("Error") or "")[:400],
            "started_at": state.get("StartedAt"),
            "finished_at": state.get("FinishedAt"),
        }

    # `docker wait` returns as soon as the EXIT CODE is known, which can be
    # before the daemon has written OOMKilled into the container's state. Read
    # immediately after a wait, a genuine memory kill therefore reports
    # `{exit_code: 137, oom_killed: False}` — indistinguishable from a deadline
    # kill, which is exactly the distinction `exit_state` exists to make. Seen
    # once on a loaded machine, on a container that lived 91ms.
    #
    # A 137 with OOMKilled False is the only ambiguous answer, so that is the
    # only one worth re-reading; every other state is taken at its word and
    # costs nothing. Production reaches this through `execution_status`, which
    # polls until `list_managed` stops calling the container running and so is
    # already past the window — this closes it for every other caller.
    _SETTLE_TRIES = 5
    _SETTLE_PAUSE = 0.05

    def _settled_state(self, container: str) -> dict:
        state = (self.inspect(container) or {}).get("State") or {}
        for _ in range(self._SETTLE_TRIES):
            if state.get("Running") or state.get("ExitCode") != 137:
                return state
            if state.get("OOMKilled"):
                return state
            time.sleep(self._SETTLE_PAUSE)
            state = (self.inspect(container) or {}).get("State") or {}
        return state

    #: How much of a log one read may return. A console in a client renders
    #: what it is given; handing it a 200 MB build log in one response is a
    #: denial of service against the thing asking for progress.
    LOG_SLICE_BYTES = 64_000

    def logs(self, container: str, tail: int = 200) -> str:
        result = self._run(["logs", "--tail", str(tail), container], check=False)
        return ((result.stdout or "") + (result.stderr or ""))[-8000:]

    def logs_since(self, container: str, offset: int = 0) -> dict:
        """A slice of the log from ``offset``, and where the next one starts.

        AN OFFSET, NOT A TIMESTAMP. `docker logs --since` takes a time, and two
        lines written in the same second are indistinguishable to it — a
        progress console would either repeat them or drop them. A byte offset
        into the accumulated output is exact, and the caller can hold it
        without holding a connection.

        The executor stays request/response: nothing here follows the log or
        keeps a stream open, because the HMAC-and-nonce protocol has no place
        to put a long-lived connection and the manager's global lock would be
        held by one for the duration.

        Returns ``{"text", "offset", "complete"}``. `complete` is False when
        the slice was truncated, so a caller knows to ask again immediately
        rather than waiting for new output that has already happened.
        """
        result = self._run(["logs", container], check=False)
        whole = (result.stdout or "") + (result.stderr or "")
        raw = whole.encode("utf-8", "replace")
        # TOLERATE JUNK. The offset arrives from an HTTP query string, so it
        # can be "abc", empty, or absent. `int()` raising there would turn a
        # client's typo into a 500 on the endpoint whose whole job is to show
        # somebody what went wrong. Start from the beginning instead — the
        # worst case is one repeated slice.
        try:
            offset = max(0, int(offset))
        except (TypeError, ValueError):
            offset = 0
        if offset > len(raw):
            # The log got SHORTER than the caller's offset. A container was
            # recreated, or docker rotated it. Starting over beats returning
            # nothing for ever, which is what a clamped offset would do.
            offset = 0
        chunk = raw[offset:offset + self.LOG_SLICE_BYTES]
        return {
            # `replace` on the way out too: a slice can cut a multi-byte
            # character in half, and a console that raises on that shows the
            # user nothing at all.
            "text": chunk.decode("utf-8", "replace"),
            "offset": offset + len(chunk),
            "complete": offset + len(chunk) >= len(raw),
        }

    def kill(self, container: str) -> None:
        self._run(["kill", container], check=False)

    def remove(self, container: str) -> None:
        """Destroy it. Idempotent: reconciliation calls this speculatively."""
        self._run(["rm", "-f", container], check=False)

    # -- finding what is out there ----------------------------------------

    def list_managed(self, *, capsule=None) -> list[dict]:
        """Every container we own, from LABELS rather than from memory.

        This is what makes a manager restart survivable: the runtime index is
        rebuilt from what Docker actually holds, so nothing has to be persisted
        and a manager that dies mid-job cannot lose track of the container it
        started.
        """
        args = ["ps", "--all", "--no-trunc",
                "--filter", f"label={LABEL_MANAGED}=1",
                # MINE ONLY. Without this the query returns every anastasia
                # runner on the daemon, including another deployment's and the
                # test suite's — and reconcile then destroys them.
                *(["--filter", f"label={LABEL_OWNER}={self.owner}"]
                  if self.owner else []),
                "--format", "{{json .}}"]
        if capsule is not None:
            # Only the new key. A legacy container cannot be selected by it,
            # which is why `list_managed()` without a capsule filter is what
            # reconcile uses to find and adopt them.
            args += ["--filter", f"label={LABEL_CAPSULE}={capsule}"]

        result = self._run(args, check=False)
        out = []
        for line in (result.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            labels = _parse_labels(row.get("Labels", ""))
            out.append({
                "id": row.get("ID") or row.get("Id") or "",
                "name": row.get("Names") or "",
                "state": (row.get("State") or "").lower(),
                # READ BOTH. A runner started before the rename carries only
                # the old key, and reporting it as capsule-less would make
                # reconcile destroy it as unowned.
                "capsule": (labels.get(LABEL_CAPSULE)
                         or labels.get(LABEL_CAPSULE_LEGACY, "")),
                "execution": labels.get(LABEL_EXEC, ""),
            })
        return out


def _parse_labels(raw: str) -> dict:
    """``docker ps`` renders labels as ``k=v,k=v``. Values may contain "=".

    They may not contain a comma — Docker escapes those — so splitting on comma
    then on the FIRST equals is correct rather than merely usually correct.
    """
    labels = {}
    for chunk in (raw or "").split(","):
        if not chunk:
            continue
        key, _, value = chunk.partition("=")
        labels[key.strip()] = value.strip()
    return labels


def describe_argv(argv) -> str:
    """For a log line. Quoted so a reader can paste it and see what ran."""
    return " ".join(shlex.quote(str(a)) for a in argv)


class KataDriver(DockerClient):
    """Each job in its own VM, with its own kernel.

    A SUBCLASS RATHER THAN A SECOND STACK, and that is the whole design. The
    alternative considered was a dedicated containerd instance driven by
    nerdctl or its gRPC API — a second client, a second set of flags to
    assemble and a second place for the hardening table to drift out of step
    with this one. What Kata actually needs is for the daemon to hand the
    container to a different OCI runtime, and ``--runtime`` says exactly that.

    So every flag in ``build_run_args`` is inherited unchanged, and the
    security model is written once. What differs is what ENFORCES each flag,
    and that difference is worth being precise about:

    * ``--memory`` / ``--pids-limit`` / ``--cpus`` — still HOST cgroups, on the
      sandbox as a whole. With Kata the sandbox contains the VMM as well as the
      workload, so the ceiling has to cover QEMU and virtiofsd too; see the
      overhead note in ``slices.py``.
    * ``--cap-drop ALL``, ``no-new-privileges``, ``--user`` — enforced INSIDE
      the guest, by the guest kernel. They stop meaning "contained relative to
      this host" and start meaning "contained relative to a kernel that is not
      this host's", which is strictly stronger.
    * ``--read-only``, ``--tmpfs``, the bind mounts — carried into the guest
      over virtio-fs. The scratch size cap remains a real ceiling.
    * ``--network none`` — no NIC is given to the VM at all.

    WHAT THIS CLASS DOES NOT DO is decide whether the runtime is there. If
    ``kata`` is not registered with the daemon, every create fails loudly with
    the daemon's own sentence, which is the correct outcome and needs no code
    here to produce it.
    """

    name = "kata"
    runtime = "kata"

    #: A guest OOM is invisible from out here, so ``oom_killed`` being False
    #: means "I could not tell", not "it had memory to spare". Measured
    #: 2026-09-10, same job, same ceiling, shm deliberately larger than the
    #: ceiling so only RAM could bind:
    #:
    #:     runc   exit 137, OOMKilled true
    #:     kata   exit 255, OOMKilled false
    #:
    #: The ceiling itself is not weaker here — it is stronger, because the VM
    #: is only as big as the limit and cannot grow past it. `--memory 64m`
    #: yields a guest whose own `free` reports 37 MB total. What is lost is not
    #: the enforcement but the REPORT: the guest kernel does the killing, and
    #: nothing crosses back to the host to say so. `docker events` emits no oom
    #: for it either, so there is nothing to subscribe to.
    observes_guest_oom = False

    #: True, but by a DIFFERENT MECHANISM — see `build_run_args` below.
    #: `--pids-limit` alone would make this False.
    enforces_guest_pids = True

    #: The shipped Kata config sets `disable_guest_seccomp = true`, so no
    #: filter reaches the workload: `/proc/self/status` reports `Seccomp: 0`
    #: against runc's `2`. Enabling it is a one-line config change and would
    #: add depth; the VM boundary is why this is a note rather than a defect.
    applies_seccomp = False

    def build_run_args(self, **kwargs) -> list[str]:
        """The shared table, plus the one thing a VM needs to keep its word.

        `--pids-limit` does not reach the workload here. Docker emits it into
        the OCI spec, but the kata shim clears `linux.resources.pids` before
        the spec reaches the guest — a blanket strip inherited from the Go
        runtime's "By now only CPU constraints are supported", alongside
        devices, block_io, network and hugepages. Memory survives because it is
        also used to size the VM; pids has no sizing role and is simply swept
        up. Measured: the guest's `pids.max` reads `max`, and a fork bomb that
        makes runc say "can't fork" prints "survived".

        That matters because `pids` is one of four dimensions a user RESERVES
        from the pool. A booked number nothing enforces is the sort of claim
        this tier work exists to delete.

        `--ulimit nproc` gets through, because `process.rlimits` is not part of
        `linux.resources` and so escapes the strip; the guest agent applies it.
        Verified on 2026-09-14: with `--user 65534` the same fork bomb is
        refused under Kata exactly as under runc.

        THREE CONDITIONS MAKE THIS SOUND, and all three are guaranteed by the
        table this class inherits rather than assumed:

        * RLIMIT_NPROC is per-UID, not per-container — but every runner is
          `--user 65534:65534`, and a Kata sandbox holds one container, so
          per-UID and per-container coincide.
        * It is bypassed by CAP_SYS_RESOURCE and CAP_SYS_ADMIN — and every
          runner is `--cap-drop ALL`.
        * It does not bind uid 0 — and no runner is root. (Checked: as root
          the same probe is unbounded, which is why this is written here and
          not offered as a general fix.)

        Kept as an override rather than folded into the shared builder so the
        Docker tier's argv is untouched: a shared kernel already enforces
        `--pids-limit`, and a second, weaker mechanism there would be noise.
        """
        return super().build_run_args(**kwargs)

    def extra_run_flags(self, limits: Limits) -> list[str]:
        return ["--ulimit", f"nproc={limits.pids}:{limits.pids}"]


#: Every tier this executor can run a job in, by the name a config uses.
#:
#: `docker` is not a sandbox in the sense the other two are — it is a container
#: beside the vault on a shared kernel — and it is kept because it is the only
#: tier that works on a host with no KVM and no gVisor, which includes this
#: project's own CI. Naming it here rather than treating it as "no tier" is
#: what lets `describe()` tell an operator the truth about which one is live.
DRIVERS = {
    "docker": DockerClient,
    "kata": KataDriver,
}


def build_driver(tier: str = "docker", **kwargs) -> DockerClient:
    """The driver a tier name asks for, or a refusal naming what exists.

    Fails closed rather than falling back. A typo in ANASTASIA_RUNTIME must not
    resolve to "docker" and quietly run other people's code beside the vault on
    a host whose operator believed they had asked for VMs.
    """
    try:
        return DRIVERS[(tier or "docker").strip().lower()](**kwargs)
    except KeyError:
        raise DriverError(
            f"{tier!r} is not an isolation tier. The tiers are: "
            f"{', '.join(sorted(DRIVERS))}."
        ) from None
