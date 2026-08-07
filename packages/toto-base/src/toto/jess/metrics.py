"""What jess meters — and, more importantly, what it does not.

Imported from QuotaConfig.ready(), so this module stays pure data — no models,
no database, no settings.

**The sending path is deliberately unmetered.** ``JessEmailBackend._record`` is
Django's EMAIL_BACKEND: it is handed an EmailMessage and nothing else, with no
request and no user, and it is what carries anonymous password resets. Three
reasons that is the wrong place for a quota:

1. It has no subject to meter. ``check_quota`` with ``user=None`` reads the
   platform-wide default policy, so every anonymous send on the host would
   share one bucket — one attacker would exhaust password recovery for
   everybody, which is worse than no cap at all.
2. A 429 or a 402 there breaks account recovery, for someone who by definition
   cannot log in to find out why.
3. The protection already exists, at the layer built for anonymous traffic:
   password reset is mounted under ``/sso/``, where nginx applies its
   ``limit_req`` zone. A per-user, per-calendar-period quota is the wrong shape
   for a per-IP, per-second problem.

So ``jess.send`` counts the four *attributable* creation sites, all of which are
already staff-only. Its value is the record — this is the only place that can
say how much mail this host sent — rather than the refusal.
"""

from toto.quota.metrics import Metric, registry

registry.register(Metric(
    code="jess.send",
    label="Message queued",
    app_label="jess",
    unit="message",
    description="One outbound message queued from an attributable, staff-facing path.",
    default_limit=100,
))
