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


class CheckState(models.Model):
    """The last verdict of one record check, as the scheduled run saw it.

    One row per check key (``toto.monit.record``), written every
    ALERT_CHECK_MINUTES by ``toto.monit.alerts``. It is what lets a mail say
    "changed" rather than "is": each run compares what it measured with this
    row and mails only the difference — see that module for the rules.
    """

    key = models.CharField(max_length=40, unique=True)
    label = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=10)
    summary = models.CharField(max_length=300, blank=True)
    #: When the check entered `status`.
    since = models.DateTimeField(default=timezone.now)
    #: When it last went from fine (ok, off) to bad: the incident's start,
    #: which the recovery mail names.
    bad_since = models.DateTimeField(null=True, blank=True)
    checked_at = models.DateTimeField(default=timezone.now)
    #: The worst bad verdict the operators were mailed and not yet told is
    #: over; "" when nothing is outstanding.
    alerted_status = models.CharField(max_length=10, blank=True)
    #: When the last mail about the problem went — the first or a reminder.
    alerted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("key",)

    def __str__(self):
        return f"{self.key}: {self.status}"
