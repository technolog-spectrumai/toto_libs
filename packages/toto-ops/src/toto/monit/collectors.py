"""Measurement functions for toto.monit.

Every collector returns a dict of Snapshot field kwargs. Each individual read
is guarded — a failing check yields None ("unknown"), never an exception. No
new dependencies: cgroup/proc file reads, the host-provided redis/requests
packages (imported lazily), the in-process prometheus registry, and the
celery `current_app` idiom from toto.celery_utils.
"""

import logging
import os
import shutil
import socket
import time
from pathlib import Path

from django.apps import apps
from django.conf import settings

logger = logging.getLogger(__name__)

_CGROUP = Path("/sys/fs/cgroup")


def _read(path):
    try:
        return Path(path).read_text().strip()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# system — measured in whatever container/process runs this code
# ---------------------------------------------------------------------------

def _cpu_usage_usec():
    """Cumulative CPU usage of this cgroup in microseconds (v2 else v1)."""
    stat = _read(_CGROUP / "cpu.stat")
    if stat:
        for line in stat.splitlines():
            if line.startswith("usage_usec"):
                return int(line.split()[1])
    v1 = _read(_CGROUP / "cpuacct" / "cpuacct.usage")  # nanoseconds
    if v1 and v1.isdigit():
        return int(v1) // 1000
    return None


def _mem_current_and_limit():
    cur = _read(_CGROUP / "memory.current")
    if cur and cur.isdigit():
        limit = _read(_CGROUP / "memory.max")
        limit_val = int(limit) if limit and limit.isdigit() else None
        return int(cur), limit_val
    cur = _read(_CGROUP / "memory" / "memory.usage_in_bytes")
    if cur and cur.isdigit():
        limit = _read(_CGROUP / "memory" / "memory.limit_in_bytes")
        limit_val = int(limit) if limit and limit.isdigit() else None
        if limit_val is not None and limit_val >= 2**60:  # "unlimited" sentinel
            limit_val = None
        return int(cur), limit_val
    # bare metal / no cgroup: host-wide fallback from /proc/meminfo
    meminfo = _read("/proc/meminfo")
    if meminfo:
        fields = {}
        for line in meminfo.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                fields[parts[0].rstrip(":")] = int(parts[1]) * 1024
        total = fields.get("MemTotal")
        available = fields.get("MemAvailable")
        if total is not None and available is not None:
            return total - available, total
    return None, None


def collect_system(cpu_window=0.5):
    out = {
        "sys_cpu_percent": None,
        "sys_load_1m": None,
        "sys_mem_used_bytes": None,
        "sys_mem_limit_bytes": None,
        "sys_disk_used_bytes": None,
        "sys_disk_total_bytes": None,
    }
    try:
        first = _cpu_usage_usec()
        if first is not None:
            time.sleep(cpu_window)
            second = _cpu_usage_usec()
            if second is not None:
                cores = os.cpu_count() or 1
                out["sys_cpu_percent"] = round(
                    max(0.0, second - first) / (cpu_window * 1e6 * cores) * 100, 2)
    except Exception:
        pass
    try:
        loadavg = _read("/proc/loadavg")
        if loadavg:
            out["sys_load_1m"] = float(loadavg.split()[0])
    except Exception:
        pass
    try:
        used, limit = _mem_current_and_limit()
        out["sys_mem_used_bytes"] = used
        out["sys_mem_limit_bytes"] = limit
    except Exception:
        pass
    try:
        usage = shutil.disk_usage("/")
        out["sys_disk_used_bytes"] = usage.used
        out["sys_disk_total_bytes"] = usage.total
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# services — network-level
# ---------------------------------------------------------------------------

def collect_db():
    from django.db import connection

    try:
        start = time.perf_counter()
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return {"db_ok": True,
                "db_latency_ms": round((time.perf_counter() - start) * 1000, 2)}
    except Exception:
        return {"db_ok": False, "db_latency_ms": None}


def _redis_client():
    caches = getattr(settings, "CACHES", {})
    backend = caches.get("default", {}).get("BACKEND", "")
    if "django_redis" in backend:
        try:
            from django_redis import get_redis_connection

            return get_redis_connection("default")
        except Exception:
            pass
    url = getattr(settings, "MONIT_REDIS_URL", "")
    if url:
        try:
            import redis

            return redis.Redis.from_url(url, socket_timeout=1.0,
                                        socket_connect_timeout=1.0)
        except Exception:
            pass
    return None


def collect_boards():
    """Are the boards and their store reachable? Reported, never diagnosed.

    NO MONGO DRIVER IS IMPORTED HERE, and that is a hard constraint rather
    than a preference: `zenobia.tests.test_no_graph` boots a probe interpreter
    and fails if `pymongo` or `motor` appears in `sys.modules`. The whole point
    of the boards decision is that Wekan owns its store and Django has no
    opinions about it — importing a driver to take its pulse would quietly
    reverse that.

    So this asks two questions the way an operator would from a shell: does
    something accept a TCP connection on the Mongo port, and does Wekan answer
    HTTP. Both are one socket, both time out fast, and both degrade to
    `False` rather than raising — a Status page that 500s because a sidecar is
    starting is worse than one that says "not right now".

    `None` means "not configured on this host", exactly as `collect_redis`
    uses it, so a build without boards reports nothing rather than reporting
    a failure.
    """
    out = {"mongo_ok": None, "mongo_latency_ms": None,
           "wekan_ok": None, "wekan_latency_ms": None}

    host = getattr(settings, "MONIT_MONGO_HOST", "") or ""
    if host:
        out["mongo_ok"], out["mongo_latency_ms"] = _tcp_probe(
            host, int(getattr(settings, "MONIT_MONGO_PORT", 27017) or 27017))

    url = getattr(settings, "MONIT_WEKAN_URL", "") or ""
    if url:
        out["wekan_ok"], out["wekan_latency_ms"] = _http_probe(url)
    return out


def collect_store():
    """Is the data store answering? One HTTP call to its own health path.

    Same contract as `collect_boards`: `None` means "no store on this host",
    `False` means "configured and not answering", and nothing here raises.
    lakeFS's `/_health` returns 200 with a body of `alive!` when it is up, and
    nothing at all when it is not — so answered-at-all is the question.
    """
    out = {"lakefs_ok": None, "lakefs_latency_ms": None}
    url = getattr(settings, "MONIT_LAKEFS_URL", "") or ""
    if url:
        out["lakefs_ok"], out["lakefs_latency_ms"] = _http_probe(url)
    return out


def _tcp_probe(host, port, timeout=1.0):
    """(reachable, ms). A refused connection is a real answer, not an error."""
    import socket

    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, round((time.perf_counter() - start) * 1000, 2)
    except Exception:              # noqa: BLE001 - refused, unresolved, timed out
        return False, None


def _http_probe(url, timeout=2.0):
    """(answered, ms). ANY status counts as answered.

    Wekan's root redirects when signed out and 200s when signed in; both mean
    the container is up, which is the only question this collector asks. A
    check that demanded 200 would report a healthy Wekan as down.
    """
    start = time.perf_counter()
    try:
        import requests

        requests.get(url, timeout=timeout, allow_redirects=False)
        return True, round((time.perf_counter() - start) * 1000, 2)
    except Exception:              # noqa: BLE001
        return False, None


def collect_redis():
    out = {"redis_ok": None, "redis_latency_ms": None,
           "redis_used_memory_bytes": None, "redis_connected_clients": None}
    client = _redis_client()
    if client is None:
        return out
    try:
        start = time.perf_counter()
        client.ping()
        out["redis_ok"] = True
        out["redis_latency_ms"] = round((time.perf_counter() - start) * 1000, 2)
        info = client.info()
        out["redis_used_memory_bytes"] = info.get("used_memory")
        out["redis_connected_clients"] = info.get("connected_clients")
    except Exception:
        out["redis_ok"] = False
    return out


def collect_celery(timeout=1.0):
    """Ping workers via the host-bound celery app (toto.celery_utils idiom)."""
    try:
        from celery import current_app

        replies = current_app.control.ping(timeout=timeout)
        return {"celery_ok": bool(replies), "celery_workers": len(replies or [])}
    except Exception:
        return {"celery_ok": None, "celery_workers": None}


def collect_tor():
    out = {"tor_ctrl_ok": None, "onion_enabled": None,
           "clearnet_enabled": None, "onion_published": None}
    if not apps.is_installed("toto.nomad"):
        return out
    try:
        from toto.nomad import service

        reachability = service.reachability()
        out["onion_enabled"] = reachability.get("onion_enabled")
        out["clearnet_enabled"] = reachability.get("clearnet_enabled")
        out["onion_published"] = service.current_onion() is not None
    except Exception:
        pass
    try:
        host = getattr(settings, "NOMAD_TOR_CONTROL_HOST", "")
        port = int(getattr(settings, "NOMAD_TOR_CONTROL_PORT", 0) or 0)
        if host and port:
            with socket.create_connection((host, port), timeout=1.0):
                out["tor_ctrl_ok"] = True
        else:
            out["tor_ctrl_ok"] = None
    except Exception:
        out["tor_ctrl_ok"] = False
    return out


def collect_aster(fresh_hours=None):
    out = {"aster_devices_total": None, "aster_devices_fresh": None}
    if not apps.is_installed("toto.aster"):
        return out
    try:
        from datetime import timedelta

        from django.utils import timezone

        from toto.aster.models import AsterDevice

        hours = fresh_hours or getattr(settings, "MONIT_ASTER_FRESH_HOURS", 24)
        out["aster_devices_total"] = AsterDevice.objects.count()
        out["aster_devices_fresh"] = AsterDevice.objects.filter(
            last_seen__gte=timezone.now() - timedelta(hours=hours)).count()
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# web tier — scraped over the compose network from the sampler container
# ---------------------------------------------------------------------------

def collect_web(url=None, host_header=None, timeout=2.0):
    out = {"web_ok": None, "web_latency_ms": None, "web_process_start": None,
           "web_rss_bytes": None, "web_cpu_seconds": None,
           "web_requests_total": None, "web_responses_5xx": None}
    url = url if url is not None else getattr(settings, "MONIT_WEB_METRICS_URL", "")
    if not url:
        return out
    host_header = host_header or getattr(settings, "MONIT_WEB_METRICS_HOST", "localhost")
    try:
        import requests

        start = time.perf_counter()
        response = requests.get(url, headers={"Host": host_header}, timeout=timeout)
        out["web_latency_ms"] = round((time.perf_counter() - start) * 1000, 2)
        out["web_ok"] = response.status_code == 200
        if not out["web_ok"]:
            return out
    except Exception:
        out["web_ok"] = False
        return out
    try:
        from prometheus_client.parser import text_string_to_metric_families

        requests_total = 0.0
        responses_5xx = 0.0
        for family in text_string_to_metric_families(response.text):
            if family.name == "process_start_time_seconds":
                out["web_process_start"] = family.samples[0].value
            elif family.name == "process_resident_memory_bytes":
                out["web_rss_bytes"] = int(family.samples[0].value)
            elif family.name == "process_cpu_seconds":
                out["web_cpu_seconds"] = family.samples[0].value
            elif family.name == "django_http_requests_total_by_method":
                requests_total += sum(s.value for s in family.samples
                                      if s.name.endswith("_total"))
                out["web_requests_total"] = int(requests_total)
            elif family.name == "django_http_responses_total_by_status":
                responses_5xx += sum(
                    s.value for s in family.samples
                    if s.name.endswith("_total")
                    and s.labels.get("status", "").startswith("5"))
                out["web_responses_5xx"] = int(responses_5xx)
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# live request metrics — THIS process's registry (web worker rendering the page)
# ---------------------------------------------------------------------------

def collect_request_metrics(max_views=10):
    out = {"uptime_seconds": None, "rss_bytes": None, "requests_total": None,
           "by_status_class": {}, "exceptions_total": None, "top_views": []}
    try:
        from prometheus_client import REGISTRY

        latency = {}
        for family in REGISTRY.collect():
            if family.name == "process_start_time_seconds":
                out["uptime_seconds"] = max(0, time.time() - family.samples[0].value)
            elif family.name == "process_resident_memory_bytes":
                out["rss_bytes"] = int(family.samples[0].value)
            elif family.name == "django_http_requests_total_by_method":
                out["requests_total"] = int(sum(
                    s.value for s in family.samples if s.name.endswith("_total")))
            elif family.name == "django_http_responses_total_by_status":
                for sample in family.samples:
                    if not sample.name.endswith("_total"):
                        continue
                    status = sample.labels.get("status", "")
                    key = f"{status[0]}xx" if status else "?"
                    out["by_status_class"][key] = (
                        out["by_status_class"].get(key, 0) + int(sample.value))
            elif family.name == "django_http_exceptions_total_by_type":
                out["exceptions_total"] = int(sum(
                    s.value for s in family.samples if s.name.endswith("_total")))
            elif family.name == "django_http_requests_latency_seconds_by_view_method":
                for sample in family.samples:
                    view = sample.labels.get("view", "")
                    if not view or view == "prometheus-django-metrics":
                        continue
                    entry = latency.setdefault(view, {"count": 0.0, "sum": 0.0})
                    if sample.name.endswith("_count"):
                        entry["count"] += sample.value
                    elif sample.name.endswith("_sum"):
                        entry["sum"] += sample.value
        top = sorted(latency.items(), key=lambda kv: kv[1]["count"], reverse=True)
        out["top_views"] = [
            {"view": view, "count": int(vals["count"]),
             "avg_ms": round(vals["sum"] / vals["count"] * 1000, 1) if vals["count"] else None}
            for view, vals in top[:max_views] if vals["count"]
        ]
    except Exception:
        logger.debug("monit: request metrics unavailable", exc_info=True)
    return out


def collect_all_for_snapshot():
    """Merge all snapshot-bound collectors (each isolated)."""
    data = {}
    # collect_boards is deliberately NOT here. Every key this merge produces
    # becomes a `Snapshot(**data)` kwarg, so a collector in this tuple must
    # correspond to model columns — adding four for a reachability check that
    # is only ever interesting *now* would be storing the wrong thing. The
    # Status view calls it live, beside redis and celery.
    for collector in (collect_system, collect_db, collect_redis,
                      collect_celery, collect_tor, collect_aster, collect_web):
        try:
            data.update(collector())
        except Exception:
            logger.warning("monit: collector %s failed", collector.__name__,
                           exc_info=True)
    return data
