"""The executor's own metrics, in Prometheus text format, written by hand.

WHY NOT `prometheus_client`. The executor must import with no Django and, by
the same argument, with as little else as possible: it is installed into its
own venv with `--no-deps` precisely so that nothing arrives beside it. The text
format is a documented, stable, line-oriented thing — a name, optional labels,
a number — and writing it costs less than the dependency costs to justify.

WHAT IS NOT A LABEL, and this is the part that matters. No user id, no job
uuid, no Capsule uuid, no operation parameters. Prometheus keeps a distinct time
series per label combination FOREVER, so a per-job label is both an unbounded
cardinality explosion and a durable record of who ran what, sitting in a
monitoring system with none of the vault's access control. Counts by FAMILY and
by OUTCOME answer every operational question ("are OCR jobs failing", "is this
host OOM-killing things") without naming a single person.

Django-free.
"""

from __future__ import annotations

import time

from . import netfilter

PREFIX = "anastasia_executor"

#: Started when the module is imported, which is close enough to process start
#: for an uptime gauge and needs no plumbing to keep accurate.
_STARTED_AT = time.time()


def _line(name: str, value, labels: dict | None = None) -> str:
    if labels:
        inner = ",".join(f'{k}="{_escape(str(v))}"' for k, v in sorted(labels.items()))
        return f"{PREFIX}_{name}{{{inner}}} {value}"
    return f"{PREFIX}_{name} {value}"


def _escape(value: str) -> str:
    """The three characters the text format reserves inside a label value."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render(manager, *, admission: dict, pressure_report: dict,
           now: float | None = None) -> str:
    """Everything this executor knows, as one scrape.

    Read live rather than accumulated: the executor keeps no counters between
    scrapes because it keeps no state at all — its whole view is rebuilt from
    container labels, which is what makes a restart survivable. So these are
    GAUGES of what is true now, not counters of what has happened, and a
    restart resets nothing because there was nothing to reset.
    """
    now = now if now is not None else time.time()
    rows = []
    try:
        managed = manager.docker.list_managed()
    except Exception:                            # noqa: BLE001
        # A scrape must never be the thing that breaks. An unreachable runtime
        # is itself worth reporting, and `runtime_reachable` below says so.
        managed = []

    running = [r for r in managed if r.get("state") == "running"]
    capsules = {r.get("capsule") for r in managed if r.get("capsule")}

    out = [
        f"# HELP {PREFIX}_up The executor is answering.",
        f"# TYPE {PREFIX}_up gauge",
        _line("up", 1),
        f"# HELP {PREFIX}_uptime_seconds Seconds since this executor started.",
        f"# TYPE {PREFIX}_uptime_seconds gauge",
        _line("uptime_seconds", int(now - _STARTED_AT)),
        f"# HELP {PREFIX}_runtime_reachable The container runtime answers.",
        f"# TYPE {PREFIX}_runtime_reachable gauge",
        _line("runtime_reachable", 1 if _reachable(manager) else 0),
        f"# HELP {PREFIX}_runners Sandboxes this executor owns, by state.",
        f"# TYPE {PREFIX}_runners gauge",
    ]
    # THE ONE PLACE THIS SCRAPE HAS COUNTERS, and they are not the executor's:
    # they live in the kernel's nftables objects and survive nothing longer
    # than the ruleset, which is reinstalled on every executor start. No
    # labels — the rules match one bridge, so there is one number per claim
    # and no way to attach a capsule to it even by accident.
    egress = netfilter.counters() if manager.egress_ready else {}
    if egress:
        out += [
            f"# HELP {PREFIX}_egress_bytes_total Bytes the proxy fetched for capsules, since the ruleset was installed.",
            f"# TYPE {PREFIX}_egress_bytes_total counter",
            _line("egress_bytes_total", egress.get("egress_bytes", 0)),
            f"# HELP {PREFIX}_egress_refused_bytes_total Bytes capsules tried to send past the proxy, since the ruleset was installed.",
            f"# TYPE {PREFIX}_egress_refused_bytes_total counter",
            _line("egress_refused_bytes_total", egress.get("refused_bytes", 0)),
        ]
    by_state: dict = {}
    for row in managed:
        by_state[row.get("state") or "unknown"] = \
            by_state.get(row.get("state") or "unknown", 0) + 1
    for state, count in sorted(by_state.items()):
        out.append(_line("runners", count, {"state": state}))
    if not by_state:
        out.append(_line("runners", 0, {"state": "running"}))

    out += [
        f"# HELP {PREFIX}_capsules_mounted Capsules with at least one sandbox.",
        f"# TYPE {PREFIX}_capsules_mounted gauge",
        _line("capsules_mounted", len(capsules)),
        f"# HELP {PREFIX}_runners_running Sandboxes currently running.",
        f"# TYPE {PREFIX}_runners_running gauge",
        _line("runners_running", len(running)),
        # ADMISSION AS A NUMBER, so an alert can fire on it. A host left
        # draining after a maintenance window is invisible otherwise: nothing
        # is broken, nothing errors, and no work runs.
        f"# HELP {PREFIX}_admitting 1 when this host takes new work.",
        f"# TYPE {PREFIX}_admitting gauge",
        _line("admitting", 1 if admission.get("state") == "open" else 0),
        f"# HELP {PREFIX}_admission_state Admission, by state (1 for the live one).",
        f"# TYPE {PREFIX}_admission_state gauge",
    ]
    for state in ("open", "draining", "stopped"):
        out.append(_line("admission_state",
                         1 if admission.get("state") == state else 0,
                         {"state": state}))

    out += [
        f"# HELP {PREFIX}_pressure_admitting 1 when the machine will take a mount.",
        f"# TYPE {PREFIX}_pressure_admitting gauge",
        _line("pressure_admitting", 1 if pressure_report.get("admitting") else 0),
        f"# HELP {PREFIX}_free_memory_mb Host memory available, in MB.",
        f"# TYPE {PREFIX}_free_memory_mb gauge",
        _line("free_memory_mb", int(pressure_report.get("free_mb") or 0)),
        f"# HELP {PREFIX}_free_disk_mb Staging disk available, in MB.",
        f"# TYPE {PREFIX}_free_disk_mb gauge",
        _line("free_disk_mb", int(pressure_report.get("free_disk_mb") or 0)),
        # The isolation tier as a labelled gauge rather than a string: a
        # dashboard can then show "how many hosts are on kata", and an alert
        # can fire on a fleet that silently fell back to containers.
        f"# HELP {PREFIX}_isolation_tier The tier this executor runs jobs in.",
        f"# TYPE {PREFIX}_isolation_tier gauge",
        _line("isolation_tier", 1, {"tier": manager.docker.name}),
        f"# HELP {PREFIX}_slice_enforced 1 when the Capsule ceiling is a kernel limit.",
        f"# TYPE {PREFIX}_slice_enforced gauge",
        _line("slice_enforced", 1 if manager.slices.enforced else 0),
    ]
    return "\n".join(out) + "\n"


def _reachable(manager) -> bool:
    try:
        return bool(manager.docker.available())
    except Exception:                            # noqa: BLE001
        return False
