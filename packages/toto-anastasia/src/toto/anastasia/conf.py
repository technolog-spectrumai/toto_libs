"""Everything anastasia reads from the host's settings, in one place.

Read through functions rather than captured at import: ``override_settings``
must be able to move any of these in a test, and a module constant captured at
import time cannot be moved at all — the trap ``antaresia/kernel.py`` documents
about its own module constants.
"""

from __future__ import annotations

from django.conf import settings

from .limits import Limits

#: What the deployment is willing to hand out in total. Zero in every dimension
#: — the default — means the host has not been given a pool, and every
#: reservation is refused with a sentence naming this setting. That is
#: deliberate: an unconfigured pool must not silently become an unbounded one.
DEFAULT_POOL: dict = {}

#: How long a reservation lasts before it expires and returns to the pool.
DEFAULT_LEASE_DAYS = 7
MAX_LEASE_DAYS = 90

#: How many live Capsules one person may hold. A cap on COUNT as well as on
#: capacity, because a hundred minimum-size capsules is its own denial of service
#: — each one is a slice, a scratch mount and a reconciliation target.
DEFAULT_MAX_CAPSULES_PER_USER = 3

#: A sample older than this makes a mounted Capsule DEGRADED: the manager is not
#: answering, so what the page shows is not what the Capsule is doing.
DEFAULT_SAMPLE_STALE_SECONDS = 180


def pool_limits() -> Limits:
    """The deployment's total capacity, as a :class:`~toto.anastasia.limits.Limits`."""
    return Limits.from_mapping(getattr(settings, "ANASTASIA_POOL", DEFAULT_POOL))


def pool_is_configured() -> bool:
    return not pool_limits().is_zero


def lease_days() -> int:
    value = int(getattr(settings, "ANASTASIA_LEASE_DAYS", DEFAULT_LEASE_DAYS))
    return max(1, min(value, MAX_LEASE_DAYS))


def max_capsules_per_user() -> int:
    """How many capsules one person may hold at once.

    READS THE OLD NAME TOO, and that is not politeness. `getattr` with a
    default cannot tell "unset" from "set to something I no longer look at", so
    a deployment that had raised `ANASTASIA_MAX_GEARS_PER_USER` to 5 would
    silently drop back to 3 — no error, no log line, just users refused a
    capsule they were entitled to. Renaming a setting read this way is a data
    loss with no exception attached.

    Precedence is new-then-old, so a config carrying both means what it most
    recently said. Drop the fallback once no live config can still use the old
    spelling.
    """
    for name in ("ANASTASIA_MAX_CAPSULES_PER_USER",
                 "ANASTASIA_MAX_GEARS_PER_USER"):
        value = getattr(settings, name, None)
        if value is not None:
            return max(1, int(value))
    return DEFAULT_MAX_CAPSULES_PER_USER


def sample_stale_seconds() -> int:
    return max(30, int(getattr(settings, "ANASTASIA_SAMPLE_STALE_SECONDS",
                               DEFAULT_SAMPLE_STALE_SECONDS)))


def executor_socket() -> str:
    """The unix socket the trusted executor listens on.

    Empty means "not deployed here", and every reservation says so rather than
    pretending. A config value rather than a constant because a second compute
    node later means pointing this at a different transport — the seam the
    subsystem was designed around.

    It replaced ANASTASIA_MANAGER_URL on 2026-09-10, when the executor stopped
    being a container on an internal network and became a root-owned host
    daemon. A path rather than a URL is the whole security difference: a
    filesystem object with an owner and a mode, instead of a port anything on
    that network could reach.
    """
    return (getattr(settings, "ANASTASIA_EXECUTOR_SOCKET", "") or "").strip()


def egress_offered() -> bool:
    """Whether this deployment offers a Capsule filtered internet access.

    A SETTING, not a question asked of the executor. The desk needs the answer
    on every page render to decide whether to show the checkbox at all, and an
    IPC round trip per render — one that also has to have an answer when the
    executor is down — would be a lot of machinery to draw a form field.

    It is deliberately only a claim about the OFFER. Whether a mount actually
    gets a filtered NIC is settled by the executor, which refuses the mount if
    its nftables table is not in the kernel. A page that promised more than
    that would be describing a configuration rather than a guarantee.
    """
    return bool(getattr(settings, "ANASTASIA_EGRESS", False))


def ctan_mirror() -> str:
    """The CTAN mirror a Capsule's LaTeX installs download from, or "".

    EMPTY MEANS NOT OFFERED, and a LaTeX install is refused with a sentence
    naming this setting — the same rule as an unconfigured pool. There is no
    default because there is no neutral one: CTAN's own download address
    redirects to a random mirror, which the Capsule's proxy allowlist would
    refuse, so a working value is always a choice of one specific host — and
    that host, with ``ctan.org`` for the package index, must be on
    ``anastasia.egress.allow``.
    """
    return (getattr(settings, "ANASTASIA_CTAN_MIRROR", "") or "").strip().rstrip("/")


def shared_secret() -> str:
    """The HMAC secret this host signs executor requests with.

    Never rendered, never sent to a browser, never logged. The Capsule page proxies
    through the server precisely so this stays here.
    """
    return getattr(settings, "ANASTASIA_SHARED_SECRET", "") or ""


def runtime_backend_path() -> str:
    return getattr(settings, "ANASTASIA_RUNTIME_BACKEND",
                   "toto.anastasia.runtime.NullRuntimeBackend")
