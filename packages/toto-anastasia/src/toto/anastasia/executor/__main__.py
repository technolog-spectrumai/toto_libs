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

from . import drivers, capsules, egress, netfilter, reconcile, service
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


def _reconcile_forever(manager: capsules.CapsuleManager, every: int) -> None:
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

    # ANASTASIA_KERNEL_NETWORK is no longer read (2026-09-10). It named the
    # Capsule's internal network, which existed so the web tier could reach a
    # long-lived kernel; there is no kernel, so every runner is `--network
    # none`. A stale variable in an old .env is ignored rather than refused:
    # unlike a retired CONFIG key, this one cannot change behaviour by being
    # present, and refusing it would break a running deployment on upgrade.
    manager = capsules.CapsuleManager(
        staging_root=os.environ.get("ANASTASIA_STAGING_ROOT",
                                    capsules.DEFAULT_STAGING_ROOT),
        docker=driver,
    )
    os.makedirs(manager.staging_root, exist_ok=True)

    if not manager.docker.available():
        log.error("the container runtime is not reachable; refusing to start")
        return 3
    # Say which tier is live, every start. An operator reading a log should
    # never have to infer whether this host runs jobs in VMs or in containers.
    log.info("anastasia: isolation tier %s", manager.docker.name)

    # EGRESS, AND ITS FILTER, BEFORE ANYTHING CAN MOUNT.
    #
    # Installed here rather than lazily at the first mount for one reason: a
    # filter applied while a Capsule is already running has a window in which
    # that Capsule is on the network unfiltered. At startup there is nothing
    # running yet — `adopt` below is what finds survivors, and it runs after
    # this line so an adopted runner meets a kernel that is already filtering.
    #
    # FAIL CLOSED HERE MEANS REFUSE TO OFFER EGRESS, not refuse to boot. A host
    # whose nft is missing or whose ruleset will not load still runs every
    # batch family perfectly — they have no network at all. Taking the whole
    # compute tier down because the optional half cannot be secured would be
    # the wrong trade; what must never happen is the NIC being handed out
    # anyway, and `CapsuleManager` refuses that when `egress_ready` is False.
    policy = egress.from_environ()
    egress_ready = False
    if policy.configured:
        try:
            netfilter.ensure(policy.bridge, policy.subnet,
                             policy.proxy_ip, policy.proxy_port)
            netfilter.verify(policy.bridge, policy.subnet,
                             policy.proxy_ip, policy.proxy_port)
            egress_ready = True
            log.info("anastasia: %s", policy.describe())
        except netfilter.NetfilterError as exc:
            log.error("anastasia: egress is OFF — %s", exc)
    else:
        log.info("anastasia: %s", policy.describe())
    # The POLICY is kept whatever happened, because it is the only copy of the
    # bridge, subnet and proxy address the executor has — and the values a
    # later repair needs. Readiness is a separate flag; conflating them made
    # the repair path unreachable on exactly the hosts that needed it.
    manager.egress = policy
    manager.egress_ready = egress_ready

    inherited = reconcile.adopt(manager)
    log.info("anastasia: generation %s adopted %s runner(s) across %s capsule(s)",
             inherited["generation"], inherited["runners"],
             len(inherited["capsules"]))
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
