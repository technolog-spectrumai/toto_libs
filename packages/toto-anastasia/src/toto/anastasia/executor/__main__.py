"""``anastasia-executord`` — the systemd unit's entrypoint.

Also reachable as ``python -m toto.anastasia.executor``. Reads its whole
configuration from the environment, because that is what an
``EnvironmentFile=`` can give it, and refuses to start without a shared secret.

Type=simple, deliberately: nothing here calls ``sd_notify``, so a unit
declaring Type=notify would wait out its whole start timeout and then be killed
as failed — with the executor working perfectly the entire time.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time

from . import drivers, gears, reconcile, service
from .drivers import docker as drivers_docker

#: Where the socket lives when nothing says otherwise. The DIRECTORY is what a
#: container binds — never this file — because a bind of the file pins the
#: inode, and every container would hold a dead socket after a restart.
DEFAULT_SOCKET = "/run/anastasia/executord.sock"


def _allowed_uids(raw: str) -> frozenset:
    """Parse ANASTASIA_EXECUTOR_PEER_UIDS, falling back to root only.

    A malformed value is ignored rather than fatal, and says so: this is a
    coarse first gate in front of HMAC, and refusing to boot over a typo in it
    would take the compute tier down to make a log line.
    """
    uids = set()
    for part in (raw or "").replace(",", " ").split():
        try:
            uids.add(int(part))
        except ValueError:
            logging.getLogger("toto.anastasia.executor").warning(
                "anastasia: ignoring unparseable peer uid %r", part)
    return frozenset(uids) or service.DEFAULT_PEER_UIDS


def _reconcile_forever(manager: gears.GearManager, every: int) -> None:
    """Deadlines and orphans, on a loop.

    A daemon thread rather than a scheduler: it has one job, it must not keep
    the process alive on shutdown, and it must never take the server down —
    hence the bare except.
    """
    while True:
        time.sleep(every)
        try:
            reconcile.tick(manager)
        except Exception:  # noqa: BLE001
            logging.getLogger("toto.anastasia.executor").exception(
                "anastasia: reconcile tick failed")


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("ANASTASIA_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    log = logging.getLogger("toto.anastasia.executor")

    secret = os.environ.get("ANASTASIA_SHARED_SECRET", "")
    if not secret:
        log.error("ANASTASIA_SHARED_SECRET is not set; refusing to start")
        return 2

    # WHICH ISOLATION TIER, named by the deployment and never guessed.
    # `build_driver` refuses an unknown name rather than falling back: a typo
    # here must not resolve to a shared kernel on a host whose operator asked
    # for VMs.
    tier = os.environ.get("ANASTASIA_RUNTIME", "docker")
    try:
        driver = drivers_docker.build_driver(tier)
    except drivers.DriverError as exc:
        log.error("%s", exc)
        return 4

    manager = gears.GearManager(
        staging_root=os.environ.get("ANASTASIA_STAGING_ROOT",
                                    gears.DEFAULT_STAGING_ROOT),
        kernel_network=os.environ.get("ANASTASIA_KERNEL_NETWORK", ""),
        docker=driver,
    )
    os.makedirs(manager.staging_root, exist_ok=True)

    if not manager.docker.available():
        log.error("the container runtime is not reachable; refusing to start")
        return 3
    # Say which tier is live, every start. An operator reading a log should
    # never have to infer whether this host runs jobs in VMs or in containers.
    log.info("anastasia: isolation tier %s", manager.docker.name)

    inherited = reconcile.adopt(manager)
    log.info("anastasia: generation %s adopted %s runner(s) across %s gear(s)",
             inherited["generation"], inherited["runners"],
             len(inherited["gears"]))
    log.info("anastasia: cgroup ceiling: %s", manager.slices.describe())

    threading.Thread(
        target=_reconcile_forever, daemon=True,
        args=(manager, int(os.environ.get("ANASTASIA_RECONCILE_SECONDS", "30"))),
    ).start()

    socket_path = os.environ.get("ANASTASIA_EXECUTOR_SOCKET",
                                 DEFAULT_SOCKET)
    uids = _allowed_uids(os.environ.get("ANASTASIA_EXECUTOR_PEER_UIDS", ""))
    httpd = service.serve(socket_path=socket_path, secret=secret,
                          manager=manager, allowed_uids=uids)
    log.info("anastasia: listening on %s (uid%s %s)", socket_path,
             "" if len(uids) == 1 else "s",
             ",".join(str(u) for u in sorted(uids)))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("anastasia: shutting down")
    return 0


if __name__ == "__main__":
    sys.exit(main())
