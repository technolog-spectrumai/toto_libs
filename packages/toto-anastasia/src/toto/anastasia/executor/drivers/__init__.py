"""What every runtime shares, and the seam a second one plugs into.

THE SPLIT. ``docker.py`` knows about a CLI, its flags and its JSON. Everything
here knows about none of that: labels, the uid a runner runs as, the
environment prefixes a runner may never be handed, and the error type callers
catch. A Kata driver labels its sandboxes the same way and refuses the same
variables, so those live here rather than being reached for through the Docker
module by whatever needs them next.

WHY IT MATTERS THAT THIS IS SMALL. The seam is not an abstraction over
containers — it is the list of things a caller is allowed to know. `gears.py`
calls ten methods and reads two dict shapes; that is the whole contract, and it
is written down in ``Driver`` below so a new runtime has something to satisfy
rather than something to imitate.

Django-free, like the rest of the executor.
"""

from __future__ import annotations

#: Labels are how a restarted executor rebuilds its view of the world. They are
#: runtime-independent by construction: whatever creates the sandbox must stamp
#: these, or reconciliation cannot find it again.
LABEL_GEAR = "anastasia.gear"
LABEL_EXEC = "anastasia.exec"
LABEL_MANAGED = "anastasia.managed"

#: The user every runner runs as. 65534 is nobody/nogroup on Debian bases —
#: chosen because it is guaranteed to exist and to own nothing.
RUNNER_UID = "65534:65534"

#: Environment variables a runner is NEVER given, asserted by a test rather
#: than merely avoided. The list is what a compromised runner would most like
#: to find, and it exists so the assertion has something to name.
#:
#: It RAISES rather than filters. A silently-dropped variable is a runner that
#: starts and then fails somewhere confusing; a refusal names the key.
FORBIDDEN_ENV_PREFIXES = (
    "DB_", "POSTGRES_", "DATABASE_", "SECRET", "DJANGO_", "VAULT_",
    "SSO_", "FIELD_ENCRYPTION", "ADMIN_", "AWS_", "S3_", "REDIS_",
    "CELERY_", "ANASTASIA_SHARED_SECRET", "TS_",
)


class DriverError(Exception):
    """The runtime said no. The message is for a log, not for a user.

    Caught by name in ``service.py`` and answered as a 502, so every driver's
    failures reach a caller the same way. ``DockerError`` subclasses it, which
    is what let the rename happen without touching that except clause.
    """


class Driver:
    """The ten methods ``gears.py`` and ``reconcile.py`` actually call.

    Written down rather than merely implemented, because the alternative is a
    second runtime discovering the contract by breaking it. Not an ABC on
    purpose — the fakes in the test suite are duck-typed and predate this, and
    forcing them to inherit would buy a type check nobody reads at the cost of
    an import in every test module.

    TWO RETURN SHAPES ARE PART OF THE CONTRACT and are easy to get subtly wrong:

    * ``exit_state`` → ``{"exit_code", "oom_killed", "error", "started_at",
      "finished_at"}``. ``oom_killed`` is the one that matters: exit code 137
      is SIGKILL and says nothing about why, so a deadline kill and an
      out-of-memory kill are indistinguishable without it, and they need
      different sentences.
    * ``list_managed`` → rows of ``{"id", "name", "state", "gear",
      "execution"}``, where ``state`` is lowercase and ``running`` is the value
      reconciliation tests for.
    """

    #: Reported by ``describe()`` and shown to operators. A driver that lies
    #: here is worse than one that fails: the platform would promise isolation
    #: it does not have.
    name = "driver"

    def available(self) -> bool:
        raise NotImplementedError

    def image_exists(self, image: str) -> bool:
        raise NotImplementedError

    def create(self, **kwargs) -> str:
        raise NotImplementedError

    def start(self, container: str) -> None:
        raise NotImplementedError

    def wait(self, container: str, timeout: int) -> dict:
        raise NotImplementedError

    def inspect(self, container: str) -> dict:
        raise NotImplementedError

    def labels(self, container: str) -> dict:
        """The sandbox's labels, as a plain dict.

        Separate from ``inspect`` on purpose: ``inspect`` returns whatever the
        runtime's own tooling says and is only useful to code that already
        knows that runtime, while THIS is the shape reconciliation reads. A
        driver that returned its raw document here would put its wire format
        back into the neutral half.
        """
        raise NotImplementedError

    def exit_state(self, container: str) -> dict:
        raise NotImplementedError

    def logs(self, container: str, tail: int = 200) -> str:
        raise NotImplementedError

    def kill(self, container: str) -> None:
        raise NotImplementedError

    def remove(self, container: str) -> None:
        raise NotImplementedError

    def list_managed(self, *, gear=None) -> list[dict]:
        raise NotImplementedError
