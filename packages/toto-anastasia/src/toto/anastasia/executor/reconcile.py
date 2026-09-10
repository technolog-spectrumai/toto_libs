"""Making what is running agree with what is supposed to be running.

Runs on a loop and at start-up. Everything it needs comes from labels and the
filesystem, so a manager that has just been restarted — or replaced — knows as
much as one that has been up for a week. That is the whole reason the manager
keeps no state of its own.

Four kinds of disagreement, and what each means:

* **past its deadline** — a runner whose ``anastasia.deadline`` label has
  passed. Killed. This is how timeouts are enforced at all: a thread per
  execution would die with the manager and leave the runner running forever.
* **orphan runner** — a container labelled for a Capsule whose staging directory
  is gone (it was unmounted, or the manager was replaced mid-unmount).
  Destroyed.
* **orphan staging** — an execution directory with no container. Its bytes are
  removed once it is older than the grace window, never immediately: an
  execution that has just been created has a directory before it has a
  container, and reaping that would be a race the caller could never win.
* **unknown Capsule** — a capsule directory whose lease the caller no longer lists.
  The caller drives this by passing ``known_capsules``; the manager does not know
  what a lease is and must not guess.
"""

from __future__ import annotations

import logging
import os
import shutil
import time

from .capsules import LABEL_DEADLINE, CapsuleManager

log = logging.getLogger("toto.anastasia.executor.reconcile")

#: How long an execution directory may exist with no container before it is
#: considered abandoned. Comfortably longer than the create-then-start window.
STAGING_GRACE_SECONDS = 600


def _label_of(manager: CapsuleManager, container_id: str, label: str) -> str:
    """One label, asked of the driver rather than dug out of its JSON.

    This read `inspect(...)["Config"]["Labels"]` until 2026-09-10 — Docker's
    document shape, in the module that is meant to know about no runtime in
    particular. A second driver would have had to fake that document to be
    reconcilable.
    """
    return (manager.docker.labels(container_id) or {}).get(label, "")


def enforce_deadlines(manager: CapsuleManager, now: float | None = None) -> int:
    """Kill every runner that has outlived the timeout its caller chose.

    EVERY running runner is checked. This used to read
    ``or row["warm"]``, exempting warm runners from their deadline — correct
    while a warm runner was a long-lived container the Capsule deliberately kept,
    and meaningless once warm pools were deleted (2026-09-10). The exemption is
    removed rather than left inert: a clause that can never be true is a clause
    nobody can reason about, and this loop is the only thing standing between a
    wedged runner and its Capsule's ceiling.
    """
    now = now if now is not None else time.time()
    killed = 0
    for row in manager.docker.list_managed():
        if row["state"] != "running":
            continue
        raw = _label_of(manager, row["id"], LABEL_DEADLINE)
        try:
            deadline = float(raw)
        except (TypeError, ValueError):
            continue
        if deadline and now > deadline:
            log.info("anastasia: killing %s — %ss past its deadline",
                     row["execution"], int(now - deadline))
            manager.docker.kill(row["id"])
            killed += 1
    return killed


def destroy_orphan_runners(manager: CapsuleManager, known_capsules=None) -> int:
    """Destroy containers whose Capsule no longer exists.

    ``known_capsules`` is the caller's list of leases that should be mounted. When
    it is None the check falls back to "is there a capsule directory", which
    catches a manager restarted after an unmount but not a Capsule released while
    the manager was down — which is exactly why the caller passes the list.
    """
    known = {str(g) for g in known_capsules} if known_capsules is not None else None
    destroyed = 0
    for row in manager.docker.list_managed():
        capsule = row["capsule"]
        if not capsule:
            # Managed, but labelled with no Capsule at all: nothing can own it.
            manager.docker.remove(row["id"])
            destroyed += 1
            continue
        orphaned = (known is not None and capsule not in known) or \
                   (known is None and not os.path.isdir(manager.capsule_dir(capsule)))
        if orphaned:
            log.info("anastasia: destroying orphan runner in capsule %s", capsule)
            manager.docker.remove(row["id"])
            destroyed += 1
    return destroyed


def kill_everything(manager: CapsuleManager) -> int:
    """Destroy every runner this executor owns. THE EMERGENCY STOP.

    Deliberately the bluntest function here. It does not ask which Capsule owns a
    runner, whether it is inside its deadline, or whether it is nearly done —
    an operator reaching for this has decided that what is running is the
    problem, and a stop that spared the wrong container would not be a stop.

    What it does NOT destroy is worth being explicit about, because it is the
    whole difference between an emergency stop and a data loss: the leases, the
    Capsule directories, the staging trees and every durable row on the Django
    side survive. A Capsule reads DEAD, its reservation is untouched, and it
    remounts. That is the same invariant `test_destruction` proves, exercised
    on purpose rather than by accident.

    Idempotent: called twice, the second is a no-op, because `remove` is.
    """
    destroyed = 0
    for row in manager.docker.list_managed():
        manager.docker.remove(row["id"])
        destroyed += 1
    if destroyed:
        log.warning("anastasia: emergency stop destroyed %s runner(s)", destroyed)
    return destroyed


def sweep_staging(manager: CapsuleManager, known_capsules=None,
                  now: float | None = None) -> dict:
    """Remove staging directories nothing is using any more."""
    now = now if now is not None else time.time()
    known = {str(g) for g in known_capsules} if known_capsules is not None else None
    live = {(r["capsule"], r["execution"]) for r in manager.docker.list_managed()}
    removed_capsules = removed_execs = 0

    root = manager.capsules_root
    if not os.path.isdir(root):
        return {"capsules": 0, "executions": 0}

    for capsule in sorted(os.listdir(root)):
        capsule_path = os.path.join(root, capsule)
        if not os.path.isdir(capsule_path):
            continue
        if known is not None and capsule not in known:
            shutil.rmtree(capsule_path, ignore_errors=True)
            removed_capsules += 1
            continue

        exec_root = os.path.join(capsule_path, "exec")
        if not os.path.isdir(exec_root):
            continue
        for execution in sorted(os.listdir(exec_root)):
            path = os.path.join(exec_root, execution)
            if (capsule, execution) in live:
                continue
            try:
                age = now - os.path.getmtime(path)
            except OSError:
                continue
            # The grace window is what stops this racing an execution that has
            # a directory but not yet a container.
            if age > STAGING_GRACE_SECONDS:
                shutil.rmtree(path, ignore_errors=True)
                removed_execs += 1

    return {"capsules": removed_capsules, "executions": removed_execs}


def adopt(manager: CapsuleManager) -> dict:
    """What this manager found already running when it started.

    Nothing is killed here — a runner mid-job whose manager was restarted is
    doing exactly what it should, and the labels are enough to keep tracking
    it. This exists so the start-up log says what was inherited rather than
    leaving an operator to wonder.
    """
    rows = manager.docker.list_managed()
    capsules = sorted({r["capsule"] for r in rows if r["capsule"]})
    return {
        "generation": manager.generation,
        "runners": len(rows),
        "running": len([r for r in rows if r["state"] == "running"]),
        "capsules": capsules,
    }


def tick(manager: CapsuleManager, known_capsules=None) -> dict:
    """One full pass. Safe to call as often as you like; nothing here is
    destructive to anything a caller still claims."""
    return {
        "deadlines_enforced": enforce_deadlines(manager),
        "orphans_destroyed": destroy_orphan_runners(manager, known_capsules),
        "staging_swept": sweep_staging(manager, known_capsules),
    }
