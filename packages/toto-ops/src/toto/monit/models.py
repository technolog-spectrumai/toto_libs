from django.db import models
from django.utils import timezone


class Snapshot(models.Model):
    """One periodic measurement of the running deployment.

    Every value field is nullable — NULL means "check unknown/skipped", never
    zero. Fields are grouped by WHERE they are measured:

    - sys_*    — the container running the sampler task (the celery worker in
                 a docker deployment; whatever process runs the task in dev).
                 sys_load_1m is host-wide (from /proc/loadavg).
    - db_* / redis_* / celery_* / tor_* / onion_* / aster_*
               — network-level service health; container-agnostic.
    - web_*    — scraped from the web tier's /metrics endpoint; counters come
                 from ONE web worker process per scrape (web_process_start
                 identifies which — rate charts pair equal values).
    """

    created = models.DateTimeField(default=timezone.now, db_index=True)

    # system — sampler container
    sys_cpu_percent = models.FloatField(null=True, blank=True)
    sys_load_1m = models.FloatField(null=True, blank=True)
    sys_mem_used_bytes = models.BigIntegerField(null=True, blank=True)
    sys_mem_limit_bytes = models.BigIntegerField(null=True, blank=True)
    sys_disk_used_bytes = models.BigIntegerField(null=True, blank=True)
    sys_disk_total_bytes = models.BigIntegerField(null=True, blank=True)

    # services — network-level
    db_ok = models.BooleanField(null=True, blank=True)
    db_latency_ms = models.FloatField(null=True, blank=True)
    redis_ok = models.BooleanField(null=True, blank=True)
    redis_latency_ms = models.FloatField(null=True, blank=True)
    redis_used_memory_bytes = models.BigIntegerField(null=True, blank=True)
    redis_connected_clients = models.IntegerField(null=True, blank=True)
    celery_ok = models.BooleanField(null=True, blank=True)
    celery_workers = models.IntegerField(null=True, blank=True)
    tor_ctrl_ok = models.BooleanField(null=True, blank=True)
    onion_enabled = models.BooleanField(null=True, blank=True)
    clearnet_enabled = models.BooleanField(null=True, blank=True)
    onion_published = models.BooleanField(null=True, blank=True)
    aster_devices_total = models.IntegerField(null=True, blank=True)
    aster_devices_fresh = models.IntegerField(null=True, blank=True)

    # web tier — one worker process per scrape
    web_ok = models.BooleanField(null=True, blank=True)
    web_latency_ms = models.FloatField(null=True, blank=True)
    web_process_start = models.FloatField(null=True, blank=True)
    web_rss_bytes = models.BigIntegerField(null=True, blank=True)
    web_cpu_seconds = models.FloatField(null=True, blank=True)
    web_requests_total = models.BigIntegerField(null=True, blank=True)
    web_responses_5xx = models.BigIntegerField(null=True, blank=True)

    class Meta:
        ordering = ("-created",)
        get_latest_by = "created"

    def __str__(self):
        return f"Snapshot {self.created:%Y-%m-%d %H:%M:%S}"
