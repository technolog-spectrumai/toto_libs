"""The only module in the platform that talks to Docker.

Through the CLI rather than a Python SDK, for the reasons the rest of this
repository already settled: no host, no wheel and no requirements file pins a
Docker library, ``deploy.py`` shells ``docker compose``, and the manager image
stays small enough to reason about. Everything here goes through
:meth:`DockerClient._run`, so a test can substitute one method.

**What a runner is allowed to be is decided HERE, not by a caller.** The
argument list below is assembled from a Family and an execution's limits; there
is no parameter anywhere in the API that reaches it. That is what makes
"callers cannot choose Docker parameters" a structural fact rather than a
filter someone has to maintain.

Django-free.
"""

from __future__ import annotations

import json
import logging
import shlex
import subprocess

from ..families import Family
from ..limits import Limits

log = logging.getLogger("toto.anastasia.executor.containers")

LABEL_GEAR = "anastasia.gear"
LABEL_EXEC = "anastasia.exec"
LABEL_MANAGED = "anastasia.managed"

#: The user every runner runs as. 65534 is nobody/nogroup on Debian bases —
#: chosen because it is guaranteed to exist and to own nothing.
RUNNER_UID = "65534:65534"

#: Environment variables a runner is NEVER given, asserted by a test rather
#: than merely avoided. The list is what a compromised runner would most like
#: to find, and it exists so the assertion has something to name.
FORBIDDEN_ENV_PREFIXES = (
    "DB_", "POSTGRES_", "DATABASE_", "SECRET", "DJANGO_", "VAULT_",
    "SSO_", "FIELD_ENCRYPTION", "ADMIN_", "AWS_", "S3_", "REDIS_",
    "CELERY_", "ANASTASIA_SHARED_SECRET", "TS_",
)


class DockerError(Exception):
    """Docker said no. The message is for a log, not for a user."""


class DockerClient:
    """A narrow, opinionated wrapper. Not a general Docker binding."""

    def __init__(self, binary: str = "docker", timeout: int = 60):
        self.binary = binary
        self.timeout = timeout

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
        * ``--network none`` unless the family genuinely needs one; only the
          python family does, and its network reaches no database.
        * ``--pids-limit`` — a fork bomb hits a wall instead of the host.
        * ``--cgroup-parent`` — the Gear's ceiling, above this runner's own.
        * ``--rm=false`` — the container is removed EXPLICITLY after its exit
          state has been read. ``--rm`` would delete the evidence (exit code,
          OOMKilled) before anyone could look at it.
        """
        args = [
            "create",
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

        for key, value in sorted((env or {}).items()):
            self._refuse_forbidden_env(key)
            args += ["--env", f"{key}={value}"]

        args.append(family.image)
        args += list(argv)
        return args

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

    def exit_state(self, container: str) -> dict:
        """Exit code and — the part that matters — whether the kernel OOM-killed it.

        Exit code 137 is SIGKILL and says nothing about why: the timeout kill
        above produces exactly the same code. ``State.OOMKilled`` is the only
        thing that distinguishes "this job needed more memory than its Gear
        allows" from "this job was stopped", and those need different sentences.
        """
        state = (self.inspect(container) or {}).get("State") or {}
        return {
            "exit_code": state.get("ExitCode"),
            "oom_killed": bool(state.get("OOMKilled")),
            "error": (state.get("Error") or "")[:400],
            "started_at": state.get("StartedAt"),
            "finished_at": state.get("FinishedAt"),
        }

    def logs(self, container: str, tail: int = 200) -> str:
        result = self._run(["logs", "--tail", str(tail), container], check=False)
        return ((result.stdout or "") + (result.stderr or ""))[-8000:]

    def kill(self, container: str) -> None:
        self._run(["kill", container], check=False)

    def remove(self, container: str) -> None:
        """Destroy it. Idempotent: reconciliation calls this speculatively."""
        self._run(["rm", "-f", container], check=False)

    # -- finding what is out there ----------------------------------------

    def list_managed(self, *, gear=None) -> list[dict]:
        """Every container we own, from LABELS rather than from memory.

        This is what makes a manager restart survivable: the runtime index is
        rebuilt from what Docker actually holds, so nothing has to be persisted
        and a manager that dies mid-job cannot lose track of the container it
        started.
        """
        args = ["ps", "--all", "--no-trunc",
                "--filter", f"label={LABEL_MANAGED}=1",
                "--format", "{{json .}}"]
        if gear is not None:
            args += ["--filter", f"label={LABEL_GEAR}={gear}"]

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
                "gear": labels.get(LABEL_GEAR, ""),
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
