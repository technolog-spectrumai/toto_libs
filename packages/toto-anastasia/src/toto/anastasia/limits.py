"""The four numbers, and the arithmetic that books them.

Every capacity question on this platform — the pool, a Gear, one execution —
is the same four-dimensional vector, so it is one type with one set of
operations rather than four fields copied into three places.

Django-free on purpose: the manager does this arithmetic too, and it runs with
no settings module.

Units, chosen so every value is an integer and no rounding is ever needed:

* ``cpu_millicores`` — 1000 = one core. Docker's ``--cpus`` takes a float and
  systemd's ``CPUQuota`` takes a percentage; both derive from this cleanly,
  and integers make the booking sums exact.
* ``ram_mb``, ``scratch_mb`` — mebibytes.
* ``pids`` — maximum simultaneous tasks (``--pids-limit`` / ``TasksMax``).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

#: Nobody may reserve less than this: a Gear below it cannot start any runner,
#: and a reservation that cannot run anything is a trap rather than a choice.
MIN_CPU_MILLICORES = 100
MIN_RAM_MB = 128
MIN_SCRATCH_MB = 64
MIN_PIDS = 32


class LimitsError(ValueError):
    """A capacity vector a person can be told about in one sentence."""


@dataclass(frozen=True)
class Limits:
    """CPU, RAM, scratch and PIDs — the whole of what Anastasia books."""

    cpu_millicores: int = 0
    ram_mb: int = 0
    scratch_mb: int = 0
    pids: int = 0

    # --- construction -----------------------------------------------------

    @classmethod
    def from_mapping(cls, data) -> "Limits":
        """Build from an untrusted dict, refusing anything that is not a
        non-negative integer. This is the API boundary: a float RAM value or a
        string CPU count must fail here rather than reach a Docker flag."""
        data = data or {}
        unknown = set(data) - {"cpu_millicores", "ram_mb", "scratch_mb", "pids"}
        if unknown:
            raise LimitsError(
                f"Unknown capacity field(s): {', '.join(sorted(unknown))}. "
                "The dimensions are cpu_millicores, ram_mb, scratch_mb and pids."
            )
        values = {}
        for field in ("cpu_millicores", "ram_mb", "scratch_mb", "pids"):
            raw = data.get(field, 0)
            # bool is an int subclass and True would silently become 1.
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise LimitsError(
                    f"{field} must be a whole number, not {type(raw).__name__}.")
            if raw < 0:
                raise LimitsError(f"{field} cannot be negative.")
            values[field] = raw
        return cls(**values)

    def as_dict(self) -> dict:
        return {
            "cpu_millicores": self.cpu_millicores,
            "ram_mb": self.ram_mb,
            "scratch_mb": self.scratch_mb,
            "pids": self.pids,
        }

    # --- arithmetic -------------------------------------------------------

    def __add__(self, other: "Limits") -> "Limits":
        return Limits(
            self.cpu_millicores + other.cpu_millicores,
            self.ram_mb + other.ram_mb,
            self.scratch_mb + other.scratch_mb,
            self.pids + other.pids,
        )

    def __sub__(self, other: "Limits") -> "Limits":
        """Clamped at zero in every dimension.

        Subtraction is used to answer "what is left", and a negative headroom
        is not a number anyone can act on — an over-booked pool (which
        reconciliation can produce after an operator edits the config down)
        should read as "nothing free", not as "minus 3 cores".
        """
        return Limits(
            max(0, self.cpu_millicores - other.cpu_millicores),
            max(0, self.ram_mb - other.ram_mb),
            max(0, self.scratch_mb - other.scratch_mb),
            max(0, self.pids - other.pids),
        )

    def fits_in(self, budget: "Limits") -> bool:
        """Whether this vector fits inside ``budget`` in EVERY dimension."""
        return (
            self.cpu_millicores <= budget.cpu_millicores
            and self.ram_mb <= budget.ram_mb
            and self.scratch_mb <= budget.scratch_mb
            and self.pids <= budget.pids
        )

    def shortfalls(self, budget: "Limits") -> list[str]:
        """Which dimensions do not fit, phrased for a person.

        Returned rather than raised so the caller can put every reason in one
        refusal instead of making somebody discover them one retry at a time.
        """
        out = []
        for field, unit in (("cpu_millicores", "mCPU"), ("ram_mb", "MB RAM"),
                            ("scratch_mb", "MB scratch"), ("pids", "PIDs")):
            want, have = getattr(self, field), getattr(budget, field)
            if want > have:
                out.append(f"{want} {unit} requested, {have} available")
        return out

    @property
    def is_zero(self) -> bool:
        return self == Limits()

    # --- runtime translation ---------------------------------------------

    @property
    def memory_bytes(self) -> int:
        """What a cgroup ceiling must be: compute RAM **plus scratch**.

        Scratch is a tmpfs on the deploy hosts (ext4 + overlayfs, so Docker's
        ``--storage-opt size=`` is unavailable), and tmpfs pages are charged to
        the cgroup that faults them in. A ceiling of ram_mb alone would let a
        full scratch OOM the compute it was supposed to be beside — so the two
        are booked separately and enforced together.
        """
        return (self.ram_mb + self.scratch_mb) * 1024 * 1024

    @property
    def cpu_quota_percent(self) -> int:
        """systemd ``CPUQuota=`` — 100 per core, rounded UP.

        Up rather than down so a 100 mCPU reservation is never quota 0, which
        systemd reads as "no CPU at all" and which would hang the runner
        instead of throttling it.
        """
        return max(1, -(-self.cpu_millicores * 100 // 1000))

    @property
    def docker_cpus(self) -> str:
        """Docker ``--cpus`` — three decimals is exact for millicores."""
        return f"{self.cpu_millicores / 1000:.3f}"


def validate_reservation(limits: Limits) -> None:
    """Refuse a reservation nothing could run in. Raises :class:`LimitsError`."""
    floor = Limits(MIN_CPU_MILLICORES, MIN_RAM_MB, MIN_SCRATCH_MB, MIN_PIDS)
    missing = []
    for field, unit in (("cpu_millicores", "mCPU"), ("ram_mb", "MB RAM"),
                        ("scratch_mb", "MB scratch"), ("pids", "PIDs")):
        want, least = getattr(limits, field), getattr(floor, field)
        if want < least:
            missing.append(f"at least {least} {unit} (asked for {want})")
    if missing:
        raise LimitsError(
            "A Compute Gear needs " + "; ".join(missing) +
            ". Below that nothing can start inside it.")


__all__ = [
    "Limits", "LimitsError", "validate_reservation", "replace",
    "MIN_CPU_MILLICORES", "MIN_RAM_MB", "MIN_SCRATCH_MB", "MIN_PIDS",
]
