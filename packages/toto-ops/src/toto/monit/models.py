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


class TaskRun(models.Model):
    """One run of a task the beat schedule names, as the worker saw it.

    Written by Celery's own signals (``toto.monit.heartbeats``, 2026-10-01): a
    row when the task starts, its outcome when it ends — for every task the
    beat schedule names, whoever sent it. The newest row of a task is the
    heartbeat the overdue check (``toto.monit.record``) reads; the Jobs page
    lists them all. One row per Celery task id, so a retry reuses its row.
    Pruned by ``monit_prune`` after MONIT_RUN_RETENTION_DAYS, except each
    task's newest.
    """

    task = models.CharField(max_length=200)
    task_id = models.CharField(max_length=255, unique=True)
    #: running, success, failed, retry — or Celery's own state, lower-cased.
    status = models.CharField(max_length=20, default="running")
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    #: What the task returned, shortened, with every secret starred.
    summary = models.CharField(max_length=300, blank=True)
    #: The exception's type and message, scrubbed the same way.
    error = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ("-started_at", "-pk")
        get_latest_by = "started_at"
        indexes = [models.Index(fields=["task", "-started_at"],
                                name="monit_taskrun_task_started")]

    def __str__(self):
        return f"{self.task} — {self.summary}" if self.summary else self.task

    @property
    def duration_s(self):
        if self.started_at and self.finished_at:
            return round((self.finished_at - self.started_at).total_seconds(), 1)
        return None


class BeatEntry(models.Model):
    """An entry of the beat schedule, as celery beat started with it.

    Written each time beat starts (``toto.monit.heartbeats``, 2026-10-01).
    ``first_seen`` is what an entry that has never run is judged from — a new
    entry is not overdue the minute it is added, and a beat restarted every
    day does not keep moving that start. ``last_seen`` is the latest start.
    """

    name = models.CharField(max_length=200, unique=True)
    task = models.CharField(max_length=200)
    first_seen = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("name",)
        verbose_name_plural = "beat entries"

    def __str__(self):
        return self.name
