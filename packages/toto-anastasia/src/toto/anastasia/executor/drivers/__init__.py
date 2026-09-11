"""What every runtime shares, and the seam a second one plugs into.

THE SPLIT. ``docker.py`` knows about a CLI, its flags and its JSON. Everything
here knows about none of that: labels, the uid a runner runs as, the
environment prefixes a runner may never be handed, and the error type callers
catch. A Kata driver labels its sandboxes the same way and refuses the same
variables, so those live here rather than being reached for through the Docker
module by whatever needs them next.

WHY IT MATTERS THAT THIS IS SMALL. The seam is not an abstraction over
containers — it is the list of things a caller is allowed to know. `capsules.py`
calls ten methods and reads two dict shapes; that is the whole contract, and it
is written down in ``Driver`` below so a new runtime has something to satisfy
rather than something to imitate.

Django-free, like the rest of the executor.
"""

from __future__ import annotations

#: Labels are how a restarted executor rebuilds its view of the world. They are
#: runtime-independent by construction: whatever creates the sandbox must stamp
#: these, or reconciliation cannot find it again.
#: Renamed from ``anastasia.capsule`` on 2026-09-10, with zero managed containers
#: on the daemon — which is the only moment this is free. A live container
#: cannot be relabelled, so reconcile reads BOTH keys for one release (see
#: ``labels()``): a runner started before the rename is adopted rather than
#: treated as unowned and destroyed.
LABEL_CAPSULE = "anastasia.capsule"

#: The pre-rename key. Read, never written. Delete once no deployment can still
#: be running a container older than the rename.
LABEL_CAPSULE_LEGACY = "anastasia.gear"
LABEL_EXEC = "anastasia.exec"
LABEL_MANAGED = "anastasia.managed"

#: WHICH executor owns this runner. The value is the executor's staging root,
#: because that is the one thing an executor uniquely owns and already knows.
#:
#: `anastasia.managed=1` is a constant, so `list_managed()` used to select every
#: anastasia runner ON THE WHOLE DAEMON. Two deployments sharing a daemon would
#: therefore reconcile each other's runners away, each correctly concluding the
#: other's containers were orphans with no staging directory. That is not
#: hypothetical: it is what a live executor did to the integration suite on
#: 2026-09-10, deleting a container mid-test every 30 seconds.
#:
#: A container with no owner label belongs to NOBODY and is left alone. Leaking
#: a container is recoverable by hand; destroying another deployment's running
#: job is not.
LABEL_OWNER = "anastasia.owner"

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
    """The ten methods ``capsules.py`` and ``reconcile.py`` actually call.

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

      **A driver that cannot see a guest's OOM must say so** — see
      ``observes_guest_oom`` below. Returning ``False`` when the honest answer
      is "I cannot tell" is how a user gets told their job was merely stopped
      when in fact it needed more memory.
    * ``list_managed`` → rows of ``{"id", "name", "state", "capsule",
      "execution"}``, where ``state`` is lowercase and ``running`` is the value
      reconciliation tests for.
    """

    #: Reported by ``describe()`` and shown to operators. A driver that lies
    #: here is worse than one that fails: the platform would promise isolation
    #: it does not have.
    name = "driver"

    #: Whether this runtime's OOM kills are visible to the HOST, and therefore
    #: whether ``exit_state()["oom_killed"]`` can be believed when it is False.
    #:
    #: True for a shared-kernel runtime: the host cgroup does the killing, so
    #: ``docker inspect`` reports it. **False for a VM tier**, where the guest
    #: kernel kills the process inside a machine the host cannot see into.
    #: Measured on 2026-09-10 with the same job under both:
    #:
    #:     runc   exit 137, OOMKilled true
    #:     kata   exit 255, OOMKilled false
    #:
    #: The temptation is to treat 255 as "must have been an OOM". Do not: 255
    #: means the guest died abnormally, which also covers a guest kernel panic
    #: and a shim failure. Interpolating a guess into a sentence a user acts on
    #: is the failure this whole tier-honesty rule exists to prevent. Say the
    #: platform cannot tell, and say what to try.
    observes_guest_oom = True

    #: Whether ``--pids-limit`` actually bounds the processes a job can create.
    #:
    #: True on a shared kernel: the host cgroup holds the workload itself.
    #: **False on a VM tier**, where the host limit applies to the sandbox —
    #: the VMM and its threads — and the workload lives in a guest with its own
    #: (unbounded) pids cgroup. Measured 2026-09-10:
    #:
    #:     docker run --runtime kata --pids-limit 48 alpine \
    #:         cat /sys/fs/cgroup/pids.max      ->  max
    #:
    #: and a fork bomb that makes runc say "can't fork" prints "survived".
    #:
    #: The host is not endangered — the guest is memory-bounded, so a fork bomb
    #: exhausts its own VM and dies there. What is wrong is the PROMISE: `pids`
    #: is one of four dimensions a user reserves from the pool, and booking a
    #: number nothing enforces is the kind of claim this tier work exists to
    #: remove. Whoever reads this flag is responsible for not making it.
    enforces_guest_pids = True

    #: Whether a syscall filter is applied to the workload itself.
    #:
    #: Measured by asking the process, not by reading a config —
    #: ``grep Seccomp /proc/self/status`` inside a runner reports ``2`` (filter
    #: mode, 1 filter) under runc and ``0`` under Kata, whose shipped config
    #: sets ``disable_guest_seccomp = true`` so container profiles are never
    #: passed to the agent.
    #:
    #: FALSE IS DEFENSIBLE ON A VM TIER and indefensible on a shared kernel.
    #: Docker's default profile exists to shrink the HOST kernel's attack
    #: surface, which is the thing a container shares. In a VM the workload
    #: reaches a guest kernel first, so a syscall exploit buys the guest — the
    #: boundary the tier is built on — rather than the machine. It is depth
    #: that is missing here, not the wall.
    #:
    #: Recorded rather than shrugged at, because "the sandbox applies seccomp"
    #: is the kind of sentence that gets repeated about a platform long after
    #: it stops being true of one of its tiers.
    applies_seccomp = True

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

    def logs_since(self, container: str, offset: int = 0) -> dict:
        """A slice of the log from ``offset``: ``{"text", "offset", "complete"}``.

        Part of the contract since 2026-09-11 because `capsules.execution_logs`
        calls it unguarded. It was implemented by the Docker drivers and by
        nothing else, so a third runtime satisfied every method written down
        here and still made the progress console a 500.
        """
        raise NotImplementedError

    def kill(self, container: str) -> None:
        raise NotImplementedError

    def remove(self, container: str) -> None:
        raise NotImplementedError

    def list_managed(self, *, capsule=None) -> list[dict]:
        raise NotImplementedError
