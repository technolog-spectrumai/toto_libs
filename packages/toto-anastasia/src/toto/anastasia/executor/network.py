"""How many bytes a Capsule has moved over the internet.

WHY THIS IS NOT AN NFTABLES COUNTER. `netfilter.py` matches on `iifname`,
which is the property that makes the filter unforgeable — and which makes it
per BRIDGE. Every capsule on the host shares that interface, so a counter on
those rules answers "how much did this host's capsules fetch", never "how much
did THIS one". Adding a per-capsule rule would mean a rule per capsule keyed on
an address the container picks up from Docker's DHCP and loses on every runner,
which is both unstable and exactly the `ip saddr` matching that module refuses.

SO IT IS READ FROM THE INTERFACE ITSELF, per runner, from the host. A
container's network namespace is reachable at ``/proc/<pid>/net/dev`` — the
kernel's own byte counters for that namespace's interfaces, with no `docker
exec`, no shell in the image and no cooperation from the workload. It is also
the same file `toto.monit.collectors` reads for the host, one namespace over.

THE COUNTERS DIE WITH THE RUNNER, which is the whole reason `NetworkMeter`
exists. A runner is thrown away after every job, taking its interface and its
counters with it; a naive reading would therefore show a capsule's usage
dropping to zero every time a job ended. The meter keeps a per-capsule total:
what its live runners have moved, plus what its finished runners had moved when
they were last seen. The result only ever goes up, until the capsule is
unmounted and the meter forgets it — which is correct, because an unmounted
capsule's next mount is a new machine.

WHAT IS AND IS NOT CLAIMED. These are bytes on the capsule's NIC: everything it
sent to and received from the proxy, headers and TLS included. That is "bytes
to the proxy", not "bytes of payload fetched" and not "bytes that reached the
internet" — a request the proxy refuses still costs the bytes of asking. A
capsule with no egress has no NIC to read and is reported as NOT MEASURED
rather than as zero.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

#: Interfaces that are not the internet. `lo` is the container talking to
#: itself, which is nobody's business and would inflate every figure by
#: whatever the workload does over localhost.
IGNORED_INTERFACES = frozenset({"lo"})


def read_namespace_bytes(pid: int, *, proc_root: str = "/proc") -> dict | None:
    """rx/tx totals for one network namespace, or None if it cannot be read.

    NONE, NEVER ZERO, on every failure path — a PID that has already exited, a
    procfs this executor cannot see, a line that will not parse. Zero here
    would be recorded as "this capsule used no internet", which is a different
    and much more confident claim than "we could not look".
    """
    path = os.path.join(proc_root, str(int(pid)), "net", "dev")
    try:
        with open(path) as handle:
            lines = handle.readlines()
    except (OSError, ValueError):
        return None

    rx = tx = 0
    found = False
    # The first two lines are the header. Each row is
    # "  eth0: <rx_bytes> <rx_packets> ... <tx_bytes> <tx_packets> ...",
    # 8 receive columns then 8 transmit columns.
    for line in lines[2:]:
        name, _, rest = line.partition(":")
        name = name.strip()
        if not name or name in IGNORED_INTERFACES:
            continue
        fields = rest.split()
        if len(fields) < 9:
            continue
        try:
            rx += int(fields[0])
            tx += int(fields[8])
        except ValueError:
            continue
        found = True
    if not found:
        return None
    return {"rx_bytes": rx, "tx_bytes": tx}


class NetworkMeter:
    """A per-capsule running total that survives its runners.

    Deliberately IN MEMORY and deliberately not persisted. The executor's own
    rule is that its world is rebuilt from container labels on restart; a byte
    total is not recoverable that way, so a restarted executor starts this
    capsule's count again from what its live runners report. The app records
    each reading into `CapsuleSample`, so the history survives even though the
    counter does not — and a restart shows as a step down in the chart, which
    is honest about what happened.
    """

    def __init__(self, *, proc_root: str = "/proc"):
        #: capsule -> {"retired": int totals, "live": {container_id: totals}}
        self._state: dict[str, dict] = {}
        #: Where namespaces are read from. Only a test points this elsewhere.
        self.proc_root = proc_root

    def forget(self, capsule) -> None:
        """Drop a capsule's total. Called on mount AND unmount.

        On unmount because the capsule is gone; on mount because a remount is a
        new machine with a new staging area, and carrying a byte total across
        it would attribute one reservation's traffic to another.
        """
        self._state.pop(str(capsule), None)

    def sample(self, driver, capsule, containers) -> dict | None:
        """Read this capsule's live runners and return its total so far.

        `containers` is the list of row dicts `list_managed` returns. Only
        running ones have a namespace to read; a finished runner's last known
        figure has already been retired by an earlier call, which is what makes
        the total monotonic across the gap where a job ends and the container
        is removed.

        Returns None when nothing could be measured AND nothing ever was — a
        capsule with no network, or a driver that cannot answer.
        """
        key = str(capsule)
        entry = self._state.setdefault(key, {"retired": {"rx_bytes": 0,
                                                         "tx_bytes": 0},
                                             "live": {}})
        seen = set()
        measured_now = False
        for row in containers:
            cid = row.get("id") or ""
            if not cid or row.get("state") != "running":
                continue
            seen.add(cid)
            try:
                pid = driver.container_pid(cid)
            except Exception:  # noqa: BLE001 — a sample must never be fatal
                log.debug("anastasia: no pid for runner %s", cid, exc_info=True)
                continue
            if not pid:
                continue
            totals = read_namespace_bytes(pid, proc_root=self.proc_root)
            if totals is None:
                continue
            entry["live"][cid] = totals
            measured_now = True

        # A runner we had a reading for and can no longer see has finished.
        # Fold its last figure into the retired total BEFORE dropping it, or
        # every completed job's traffic silently disappears from the chart.
        for cid in [c for c in entry["live"] if c not in seen]:
            last = entry["live"].pop(cid)
            entry["retired"]["rx_bytes"] += last["rx_bytes"]
            entry["retired"]["tx_bytes"] += last["tx_bytes"]

        rx = entry["retired"]["rx_bytes"] + sum(
            v["rx_bytes"] for v in entry["live"].values())
        tx = entry["retired"]["tx_bytes"] + sum(
            v["tx_bytes"] for v in entry["live"].values())
        if not measured_now and not entry["live"] and rx == 0 and tx == 0:
            # Nothing read now and nothing ever read. Not measured.
            return None
        return {"net_rx_bytes": rx, "net_tx_bytes": tx}
