"""``python -m toto.anastasia.manager`` — the container's entrypoint.

Reads its whole configuration from the environment, because that is what a
compose service can give it, and refuses to start without a shared secret.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time

from . import gears, reconcile, service


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
            logging.getLogger("toto.anastasia.manager").exception(
                "anastasia: reconcile tick failed")


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("ANASTASIA_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    log = logging.getLogger("toto.anastasia.manager")

    secret = os.environ.get("ANASTASIA_SHARED_SECRET", "")
    if not secret:
        log.error("ANASTASIA_SHARED_SECRET is not set; refusing to start")
        return 2

    manager = gears.GearManager(
        staging_root=os.environ.get("ANASTASIA_STAGING_ROOT",
                                    gears.DEFAULT_STAGING_ROOT),
        kernel_network=os.environ.get("ANASTASIA_KERNEL_NETWORK", ""),
    )
    os.makedirs(manager.staging_root, exist_ok=True)

    if not manager.docker.available():
        log.error("the Docker daemon is not reachable; refusing to start")
        return 3

    inherited = reconcile.adopt(manager)
    log.info("anastasia: generation %s adopted %s runner(s) across %s gear(s)",
             inherited["generation"], inherited["runners"],
             len(inherited["gears"]))
    log.info("anastasia: cgroup ceiling: %s", manager.slices.describe())

    threading.Thread(
        target=_reconcile_forever, daemon=True,
        args=(manager, int(os.environ.get("ANASTASIA_RECONCILE_SECONDS", "30"))),
    ).start()

    host = os.environ.get("ANASTASIA_BIND", "0.0.0.0")
    port = int(os.environ.get("ANASTASIA_PORT", "8900"))
    httpd = service.serve(host=host, port=port, secret=secret, manager=manager)
    log.info("anastasia: listening on %s:%s", host, port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("anastasia: shutting down")
    return 0


if __name__ == "__main__":
    sys.exit(main())
