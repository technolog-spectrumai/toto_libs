"""Admission control an operator can reach: drain, resume, emergency stop.

TWO SWITCHES, and the difference between them is the whole module.

**Drain** stops new work and lets running work finish. It is what an operator
sets before a reboot, an upgrade or a migration: the machine empties itself,
nobody's job is destroyed, and the last one out turns off the light.

**Stop** is the emergency: no new work AND every running job killed. It exists
for the case where what is running is the problem — a job nobody can identify,
a runaway that reconciliation has not caught, a security event. It throws away
work on purpose, which is why it is a separate verb with a separate name rather
than a flag on drain.

BOTH PERSIST ACROSS A RESTART, and that is not a convenience. The failure it
prevents: an operator stops the executor because something is wrong, the unit
restarts (``Restart=on-failure``, five seconds later), and the machine quietly
resumes handing out compute. A switch that a crash can undo is not a switch.

So the state is a FILE, not memory. Written under the staging root, which the
same deployment already owns and which survives the process but not the
machine's provisioning — the right lifetime for "this host is not taking work".

Django-free.
"""

from __future__ import annotations

import json
import logging
import os
import time

log = logging.getLogger("toto.anastasia.executor.control")

#: Admission is open. The absence of a file means this, so an executor on a
#: fresh host takes work without anybody having to say so.
OPEN = "open"
#: No new work; running work finishes.
DRAINING = "draining"
#: No new work; running work was killed.
STOPPED = "stopped"

STATES = (OPEN, DRAINING, STOPPED)

#: Where the switch lives. Under the staging root because that directory is
#: already this deployment's, already root-owned, and already outlives the
#: process — the three properties the state needs.
FILENAME = "admission.json"


def _path(staging_root: str) -> str:
    return os.path.join(staging_root, FILENAME)


def read(staging_root: str) -> dict:
    """The current switch, and why it was thrown.

    An unreadable or corrupt file reads as OPEN rather than as STOPPED, and
    that direction is a real decision. The alternative — fail closed — would
    mean a single bad write takes the compute tier down until somebody notices,
    with no way for the platform to tell an operator why. Refusing work is not
    a safe default when the thing being protected is availability; the two
    switches here are deliberate acts, and a file nobody wrote is not one.
    """
    try:
        with open(_path(staging_root), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {"state": OPEN, "reason": "", "since": None}
    except (OSError, ValueError) as exc:
        log.warning("anastasia: unreadable admission state (%s); reading as open", exc)
        return {"state": OPEN, "reason": "", "since": None}

    state = data.get("state")
    if state not in STATES:
        log.warning("anastasia: unknown admission state %r; reading as open", state)
        return {"state": OPEN, "reason": "", "since": None}
    return {"state": state,
            "reason": str(data.get("reason") or "")[:400],
            "since": data.get("since")}


def write(staging_root: str, state: str, *, reason: str = "",
          now: float | None = None) -> dict:
    """Throw the switch, durably.

    Written to a temporary file and renamed, because a half-written state file
    read back as OPEN would silently resume a host somebody had stopped.
    """
    if state not in STATES:
        raise ValueError(f"{state!r} is not an admission state")
    os.makedirs(staging_root, exist_ok=True)
    payload = {"state": state, "reason": reason[:400],
               "since": now if now is not None else time.time()}
    target = _path(staging_root)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, target)
    log.warning("anastasia: admission is now %s%s", state,
                f" ({reason})" if reason else "")
    return payload


def accepts_work(staging_root: str) -> bool:
    return read(staging_root)["state"] == OPEN


def refusal(state: dict) -> str:
    """What a user is told when the switch is against them.

    Never "try again shortly" for a STOPPED host: pressure is temporary and
    self-clearing, an emergency stop is a person's decision and stays until
    another person reverses it. Telling somebody to retry into a stopped
    machine is how a refusal becomes a support ticket.
    """
    reason = state.get("reason") or ""
    if state.get("state") == DRAINING:
        return ("This machine is being drained for maintenance, so it is not "
                "taking new work. Your reservation is untouched and running "
                "jobs are finishing normally."
                + (f" {reason}" if reason else ""))
    return ("Compute on this machine has been stopped by an administrator. "
            "Your reservation is untouched, but nothing will run until it is "
            "turned back on." + (f" {reason}" if reason else ""))
