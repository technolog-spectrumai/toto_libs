"""The collectors: what each probe reads, and that none of them ever raises.

Named tests_more_collectors.py (sibling of tests.py) and meant for the gate's
host-owned block beside toto.monit.tests. Every outbound call — the cgroup and
/proc files, sockets, HTTP, Redis, Celery — is replaced at the module seam, so
nothing here depends on the machine it runs on or waits on the clock.
"""

import tempfile
from collections import namedtuple
from pathlib import Path
from unittest import mock

from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings

from . import collectors

DiskUsage = namedtuple("DiskUsage", "total used free")


def fake_reads(files):
    """A stand-in for collectors._read backed by {path: text}."""
    return lambda path: files.get(str(path))


class CgroupReadTests(SimpleTestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        patcher = mock.patch.object(collectors, "_CGROUP", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_cgroup_v2_cpu_usage_is_read_in_microseconds(self):
        self.write("cpu.stat", "usage_usec 123456\nuser_usec 100000\n")
        self.assertEqual(collectors._cpu_usage_usec(), 123456)

    def test_cgroup_v1_cpu_usage_is_converted_from_nanoseconds(self):
        self.write("cpuacct/cpuacct.usage", "5000000")
        self.assertEqual(collectors._cpu_usage_usec(), 5000)

    def test_no_cgroup_cpu_is_unknown(self):
        self.assertIsNone(collectors._cpu_usage_usec())

    def test_cgroup_v2_memory_with_and_without_a_limit(self):
        self.write("memory.current", "1048576")
        self.write("memory.max", "max")
        self.assertEqual(collectors._mem_current_and_limit(), (1048576, None))
        self.write("memory.max", "2097152")
        self.assertEqual(collectors._mem_current_and_limit(), (1048576, 2097152))

    def test_cgroup_v1_unlimited_sentinel_means_no_limit(self):
        self.write("memory/memory.usage_in_bytes", "4096")
        self.write("memory/memory.limit_in_bytes", str(2 ** 63 - 4096))
        self.assertEqual(collectors._mem_current_and_limit(), (4096, None))
        self.write("memory/memory.limit_in_bytes", "8192")
        self.assertEqual(collectors._mem_current_and_limit(), (4096, 8192))

    def test_bare_metal_falls_back_to_meminfo(self):
        meminfo = "MemTotal:  1000 kB\nMemFree: 100 kB\nMemAvailable:  250 kB\n"
        with mock.patch.object(collectors, "_read",
                               fake_reads({"/proc/meminfo": meminfo})):
            self.assertEqual(collectors._mem_current_and_limit(),
                             (750 * 1024, 1000 * 1024))

    def test_nothing_readable_is_unknown_memory(self):
        with mock.patch.object(collectors, "_read", fake_reads({})):
            self.assertEqual(collectors._mem_current_and_limit(), (None, None))

    def test_read_swallows_a_missing_file(self):
        self.assertIsNone(collectors._read(self.root / "nope"))


class CollectSystemTests(SimpleTestCase):
    def test_cpu_percent_is_usage_over_the_window_across_all_cores(self):
        with mock.patch.object(collectors, "_cpu_usage_usec",
                               side_effect=[1_000_000, 1_500_000]), \
             mock.patch.object(collectors.time, "sleep") as sleep, \
             mock.patch.object(collectors.os, "cpu_count", return_value=2), \
             mock.patch.object(collectors, "_read",
                               fake_reads({"/proc/loadavg": "0.42 0.30 0.20 1/100 999"})), \
             mock.patch.object(collectors, "_mem_current_and_limit",
                               return_value=(10, 20)), \
             mock.patch.object(collectors.shutil, "disk_usage",
                               return_value=DiskUsage(100, 40, 60)):
            out = collectors.collect_system(cpu_window=0.5)

        sleep.assert_called_once_with(0.5)
        self.assertEqual(out, {
            "sys_cpu_percent": 50.0, "sys_load_1m": 0.42,
            "sys_mem_used_bytes": 10, "sys_mem_limit_bytes": 20,
            "sys_disk_used_bytes": 40, "sys_disk_total_bytes": 100,
        })

    def test_every_failing_read_is_unknown_never_an_exception(self):
        with mock.patch.object(collectors, "_cpu_usage_usec",
                               side_effect=RuntimeError("cgroup")), \
             mock.patch.object(collectors, "_read", return_value="not-a-number"), \
             mock.patch.object(collectors, "_mem_current_and_limit",
                               side_effect=OSError("mem")), \
             mock.patch.object(collectors.shutil, "disk_usage",
                               side_effect=OSError("disk")):
            out = collectors.collect_system(cpu_window=0.5)

        self.assertEqual(set(out.values()), {None})

    def test_no_cgroup_means_no_cpu_figure_and_no_wait(self):
        with mock.patch.object(collectors, "_cpu_usage_usec", return_value=None), \
             mock.patch.object(collectors.time, "sleep") as sleep:
            out = collectors.collect_system()
        sleep.assert_not_called()
        self.assertIsNone(out["sys_cpu_percent"])


class CollectDbTests(TestCase):
    def test_a_reachable_database_reports_its_latency(self):
        out = collectors.collect_db()
        self.assertTrue(out["db_ok"])
        self.assertGreaterEqual(out["db_latency_ms"], 0)

    def test_an_unreachable_database_is_reported_down(self):
        with mock.patch.object(connection, "cursor", side_effect=RuntimeError("down")):
            self.assertEqual(collectors.collect_db(), {"db_ok": False, "db_latency_ms": None})


class FakeRedis:
    def __init__(self, fail=False):
        self.fail = fail

    def ping(self):
        if self.fail:
            raise ConnectionError("refused")
        return True

    def info(self):
        return {"used_memory": 2048, "connected_clients": 3}


class CollectRedisTests(SimpleTestCase):
    def test_no_redis_configured_reports_nothing(self):
        with mock.patch.object(collectors, "_redis_client", return_value=None):
            self.assertEqual(set(collectors.collect_redis().values()), {None})

    def test_a_live_redis_reports_memory_and_clients(self):
        with mock.patch.object(collectors, "_redis_client", return_value=FakeRedis()):
            out = collectors.collect_redis()
        self.assertTrue(out["redis_ok"])
        self.assertEqual(out["redis_used_memory_bytes"], 2048)
        self.assertEqual(out["redis_connected_clients"], 3)

    def test_a_dead_redis_is_down_not_unknown(self):
        with mock.patch.object(collectors, "_redis_client",
                               return_value=FakeRedis(fail=True)):
            out = collectors.collect_redis()
        self.assertIs(out["redis_ok"], False)
        self.assertIsNone(out["redis_used_memory_bytes"])

    @override_settings(CACHES={"default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
        MONIT_REDIS_URL="")
    def test_without_a_redis_cache_or_url_there_is_no_client(self):
        self.assertIsNone(collectors._redis_client())

    @override_settings(CACHES={"default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
        MONIT_REDIS_URL="redis://redis.invalid:6379/0")
    def test_a_monit_redis_url_builds_a_lazy_client(self):
        client = collectors._redis_client()  # no connection is made here
        self.assertIsNotNone(client)
        self.assertEqual(client.connection_pool.connection_kwargs["host"],
                         "redis.invalid")


class CollectCeleryTests(SimpleTestCase):
    def test_worker_replies_are_counted(self):
        with mock.patch("celery.current_app.control.ping",
                        return_value=[{"w1": "pong"}, {"w2": "pong"}]):
            self.assertEqual(collectors.collect_celery(),
                             {"celery_ok": True, "celery_workers": 2})

    def test_silence_is_down(self):
        with mock.patch("celery.current_app.control.ping", return_value=[]):
            self.assertEqual(collectors.collect_celery(),
                             {"celery_ok": False, "celery_workers": 0})

    def test_a_broker_error_is_unknown(self):
        with mock.patch("celery.current_app.control.ping",
                        side_effect=OSError("no broker")):
            self.assertEqual(collectors.collect_celery(),
                             {"celery_ok": None, "celery_workers": None})


class NotInstalledTests(SimpleTestCase):
    def test_tor_and_aster_report_nothing_where_their_apps_are_absent(self):
        with mock.patch.object(collectors.apps, "is_installed", return_value=False):
            self.assertEqual(set(collectors.collect_tor().values()), {None})
            self.assertEqual(set(collectors.collect_aster().values()), {None})


class ProbeTests(SimpleTestCase):
    def test_a_refused_tcp_connection_is_a_real_answer(self):
        with mock.patch("socket.create_connection", side_effect=ConnectionRefusedError()):
            self.assertEqual(collectors._tcp_probe("mongo", 27017), (False, None))

    def test_an_accepted_tcp_connection_is_timed(self):
        with mock.patch("socket.create_connection") as connect:
            ok, ms = collectors._tcp_probe("mongo", 27017, timeout=0.5)
        connect.assert_called_once_with(("mongo", 27017), timeout=0.5)
        self.assertTrue(ok)
        self.assertGreaterEqual(ms, 0)

    def test_any_http_status_counts_as_answered(self):
        with mock.patch("requests.get", return_value=mock.Mock(status_code=302)) as get:
            ok, _ = collectors._http_probe("http://wekan/")
        self.assertTrue(ok)
        self.assertFalse(get.call_args.kwargs["allow_redirects"])

    def test_no_http_answer_is_down(self):
        with mock.patch("requests.get", side_effect=OSError("timeout")):
            self.assertEqual(collectors._http_probe("http://wekan/"), (False, None))

    def test_unconfigured_boards_and_store_are_not_probed(self):
        with mock.patch("socket.create_connection") as connect, \
             mock.patch("requests.get") as get:
            self.assertEqual(set(collectors.collect_boards().values()), {None})
            self.assertEqual(set(collectors.collect_store().values()), {None})
        connect.assert_not_called()
        get.assert_not_called()

    @override_settings(MONIT_MONGO_HOST="mongo", MONIT_MONGO_PORT=27018,
                       MONIT_WEKAN_URL="http://wekan/", MONIT_LAKEFS_URL="http://lakefs/_health")
    def test_configured_boards_and_store_are_each_probed_once(self):
        with mock.patch.object(collectors, "_tcp_probe", return_value=(False, None)) as tcp, \
             mock.patch.object(collectors, "_http_probe", return_value=(True, 1.5)) as http:
            boards = collectors.collect_boards()
            store = collectors.collect_store()

        tcp.assert_called_once_with("mongo", 27018)
        self.assertEqual([c.args[0] for c in http.call_args_list],
                         ["http://wekan/", "http://lakefs/_health"])
        self.assertEqual(boards, {"mongo_ok": False, "mongo_latency_ms": None,
                                  "wekan_ok": True, "wekan_latency_ms": 1.5})
        self.assertEqual(store, {"lakefs_ok": True, "lakefs_latency_ms": 1.5})


METRICS = """\
# HELP process_start_time_seconds Start time.
# TYPE process_start_time_seconds gauge
process_start_time_seconds 1700000000.0
# HELP process_resident_memory_bytes RSS.
# TYPE process_resident_memory_bytes gauge
process_resident_memory_bytes 5.24288e+07
# HELP process_cpu_seconds_total CPU.
# TYPE process_cpu_seconds_total counter
process_cpu_seconds_total 12.5
# HELP django_http_requests_total_by_method_total Requests.
# TYPE django_http_requests_total_by_method_total counter
django_http_requests_total_by_method_total{method="GET"} 90.0
django_http_requests_total_by_method_total{method="POST"} 10.0
# HELP django_http_responses_total_by_status_total Responses.
# TYPE django_http_responses_total_by_status_total counter
django_http_responses_total_by_status_total{status="200"} 95.0
django_http_responses_total_by_status_total{status="500"} 3.0
django_http_responses_total_by_status_total{status="503"} 2.0
# HELP django_http_exceptions_total_by_type_total Exceptions.
# TYPE django_http_exceptions_total_by_type_total counter
django_http_exceptions_total_by_type_total{type="ValueError"} 4.0
# HELP django_http_requests_latency_seconds_by_view_method Latency.
# TYPE django_http_requests_latency_seconds_by_view_method histogram
django_http_requests_latency_seconds_by_view_method_bucket{view="home",method="GET",le="+Inf"} 8.0
django_http_requests_latency_seconds_by_view_method_count{view="home",method="GET"} 8.0
django_http_requests_latency_seconds_by_view_method_sum{view="home",method="GET"} 0.4
django_http_requests_latency_seconds_by_view_method_bucket{view="rare",method="GET",le="+Inf"} 1.0
django_http_requests_latency_seconds_by_view_method_count{view="rare",method="GET"} 1.0
django_http_requests_latency_seconds_by_view_method_sum{view="rare",method="GET"} 2.0
django_http_requests_latency_seconds_by_view_method_bucket{view="prometheus-django-metrics",method="GET",le="+Inf"} 50.0
django_http_requests_latency_seconds_by_view_method_count{view="prometheus-django-metrics",method="GET"} 50.0
django_http_requests_latency_seconds_by_view_method_sum{view="prometheus-django-metrics",method="GET"} 0.1
"""


class CollectWebTests(SimpleTestCase):
    def test_no_scrape_url_reports_nothing(self):
        with mock.patch("requests.get") as get:
            out = collectors.collect_web(url="")
        get.assert_not_called()
        self.assertEqual(set(out.values()), {None})

    def test_a_scrape_is_parsed_and_only_5xx_are_counted_as_errors(self):
        response = mock.Mock(status_code=200, text=METRICS)
        with mock.patch("requests.get", return_value=response) as get:
            out = collectors.collect_web(url="http://web:8000/metrics",
                                         host_header="portal.test")

        self.assertEqual(get.call_args.kwargs["headers"], {"Host": "portal.test"})
        self.assertTrue(out["web_ok"])
        self.assertEqual(out["web_process_start"], 1700000000.0)
        self.assertEqual(out["web_rss_bytes"], 52428800)
        self.assertEqual(out["web_cpu_seconds"], 12.5)
        self.assertEqual(out["web_requests_total"], 100)
        self.assertEqual(out["web_responses_5xx"], 5)

    def test_a_non_200_scrape_is_down_with_no_figures(self):
        with mock.patch("requests.get", return_value=mock.Mock(status_code=404, text="")):
            out = collectors.collect_web(url="http://web:8000/metrics")
        self.assertIs(out["web_ok"], False)
        self.assertIsNotNone(out["web_latency_ms"])
        self.assertIsNone(out["web_requests_total"])

    def test_an_unreachable_web_tier_is_down(self):
        with mock.patch("requests.get", side_effect=OSError("refused")):
            out = collectors.collect_web(url="http://web:8000/metrics")
        self.assertIs(out["web_ok"], False)
        self.assertIsNone(out["web_latency_ms"])

    @override_settings(MONIT_WEB_METRICS_URL="http://web:8000/metrics",
                       MONIT_WEB_METRICS_HOST="internal.host")
    def test_the_url_and_host_default_from_settings(self):
        with mock.patch("requests.get",
                        return_value=mock.Mock(status_code=200, text="")) as get:
            collectors.collect_web()
        self.assertEqual(get.call_args.args[0], "http://web:8000/metrics")
        self.assertEqual(get.call_args.kwargs["headers"], {"Host": "internal.host"})


class FakeRegistry:
    def __init__(self, text):
        self.text = text

    def collect(self):
        from prometheus_client.parser import text_string_to_metric_families

        return list(text_string_to_metric_families(self.text))


class RequestMetricsTests(SimpleTestCase):
    def test_this_processes_registry_is_summarised(self):
        with mock.patch("prometheus_client.REGISTRY", FakeRegistry(METRICS)), \
             mock.patch.object(collectors.time, "time", return_value=1700000060.0):
            out = collectors.collect_request_metrics()

        self.assertEqual(out["uptime_seconds"], 60.0)
        self.assertEqual(out["rss_bytes"], 52428800)
        self.assertEqual(out["requests_total"], 100)
        self.assertEqual(out["by_status_class"], {"2xx": 95, "5xx": 5})
        self.assertEqual(out["exceptions_total"], 4)
        # Busiest first; the metrics endpoint's own traffic is not a view.
        self.assertEqual(out["top_views"], [
            {"view": "home", "count": 8, "avg_ms": 50.0},
            {"view": "rare", "count": 1, "avg_ms": 2000.0},
        ])

    def test_max_views_caps_the_table(self):
        with mock.patch("prometheus_client.REGISTRY", FakeRegistry(METRICS)):
            out = collectors.collect_request_metrics(max_views=1)
        self.assertEqual([v["view"] for v in out["top_views"]], ["home"])

    def test_a_broken_registry_yields_the_empty_shape(self):
        broken = mock.Mock()
        broken.collect.side_effect = RuntimeError("registry")
        with mock.patch("prometheus_client.REGISTRY", broken), \
             self.assertLogs("toto.monit.collectors", level="DEBUG"):
            out = collectors.collect_request_metrics()
        self.assertEqual(out["top_views"], [])
        self.assertIsNone(out["requests_total"])


class SnapshotMergeTests(SimpleTestCase):
    def test_one_failing_collector_costs_only_its_own_fields(self):
        fakes = {
            "collect_system": lambda: {"sys_load_1m": 1.0},
            "collect_db": lambda: {"db_ok": True},
            "collect_redis": mock.Mock(side_effect=RuntimeError("boom")),
            "collect_celery": lambda: {"celery_ok": None},
            "collect_tor": lambda: {"tor_ctrl_ok": None},
            "collect_aster": lambda: {"aster_devices_total": None},
            "collect_web": lambda: {"web_ok": None},
        }
        for name, fake in fakes.items():
            if not isinstance(fake, mock.Mock):
                fake = mock.Mock(side_effect=fake, __name__=name)
            else:
                fake.__name__ = name
            patcher = mock.patch.object(collectors, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

        with self.assertLogs("toto.monit.collectors", level="WARNING") as logs:
            data = collectors.collect_all_for_snapshot()

        self.assertEqual(data, {"sys_load_1m": 1.0, "db_ok": True, "celery_ok": None,
                                "tor_ctrl_ok": None, "aster_devices_total": None,
                                "web_ok": None})
        self.assertIn("collect_redis", logs.output[0])

    def test_the_boards_are_never_part_of_a_snapshot(self):
        """Every merged key becomes a Snapshot column, and the boards have none."""
        from .models import Snapshot

        columns = {f.name for f in Snapshot._meta.get_fields()}
        self.assertFalse({"mongo_ok", "wekan_ok", "lakefs_ok"} & columns)
