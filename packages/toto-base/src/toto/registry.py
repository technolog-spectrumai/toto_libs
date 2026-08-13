"""App lists and capability checks for host projects.

Hosts compose INSTALLED_APPS from these lists (interleaving the Django and
third-party apps they need); the lists preserve the exact contents and order
the portal host has always used.
"""

# toto-base apps every host installs (portal settings base block, original order).
CORE_APPS = [
    "toto.core",
    "toto.api",
    "toto.backup",
    "toto.gervazy",       # encryption and vault management
    "toto.vault",
    "toto.people",
    "toto.locations",
    "toto.socialhub",
    "toto.events",
    "toto.verbena",
    "toto.quota",
]

# The historical auth block (ships in toto-auth since 1.8): the standalone
# OIDC-provider default. Strategy-aware hosts compose [*CORE_APPS,
# *toto.auth_config.auth_apps(cfg)] instead to pick provider/consumer/local.
# social_login is inert unless a provider's OAuth credentials are configured.
AUTH_APPS = [
    "toto.sso_core",
    "toto.sso_master",
    "toto.social_login",
]

# The historical contract, unchanged: core apps + the provider auth block.
BASE_APPS = [*CORE_APPS, *AUTH_APPS]

# Feature key (see toto.features.Features) -> apps the feature installs.
# Includes the third-party companions a feature block always shipped with.
FEATURE_APPS = {
    "workflows": [
        "jsoneditor",        # JSON widget — imported by toto.workflows.admin
        "toto.mandragora",   # Jupyter kernel server — runs workflow lambda nodes
        "toto.workflows",    # DAG workflow engine
    ],
    "chat": ["toto.forum"],
    "weather": ["toto.weather"],
    # The media section, as of 1.21. There is no "media" key any more: the apps that
    # want a celery worker (manta + fileservices + transcription) moved to the
    # toto-media-ops package, which no host pins. BUILD_MEDIA still resolves, as the
    # umbrella default for "vod"; see toto.features. vod and ocr ship together in
    # toto-media, but each installs on its own flag.
    "vod": ["toto.vod"],        # video-on-demand vault play plugin + library
    # The two toto-media-ops entries below install nothing on any current host,
    # because no host pins that wheel. They are the contract for one that does — see
    # packages/toto-media-ops/README.md. transcription has no entry on purpose: it
    # has no UI at all, so there is nothing to switch on.
    "manta": ["toto.manta"],                  # ffmpeg/ffprobe command builder
    "fileservices": ["toto.fileservices"],    # run substrate + the vault wand
    "graph": [
        "toto.ravioli",         # sole Neo4j boundary
        "toto.sql_neo4j_sync",  # SQL→Neo4j projection/sync layer
        "toto.neo_editor",      # dual-mode .neojson vault editor
        "toto.bento",           # first-class Neo4j graph editor
        "toto.ingestor",        # text → Bento-validated graph patch
    ],
    # toto.ocr left toto-graph for toto-media in 1.21 and no longer implies the graph
    # — its ingestor handoff greys itself out. Opt-in even though its package-mate
    # vod defaults on, because it brings a tesseract apt layer with it.
    "ocr": ["toto.ocr"],
    "connectors": ["toto.connectors"],
    "formica": ["toto.formica"],
    "sabbia": ["toto.sabbia"],
    "steven": ["toto.steven"],
    "vicuna": ["toto.vicuna"],
    "editor": ["toto.editor"],
    # Content screening (BUILD_ANTIVIRUS). Flag-gated rather than core because
    # scanning is a policy a host adopts, not a fact of storage — but note what
    # OFF means: content is written unscreened and silently. toto.vault.scanning
    # is the seam, and it degrades to clean-and-UNSCANNED so nothing marks a
    # file as checked when nobody checked it.
    "antivirus": ["toto.antivirus"],
    "monit": ["toto.monit"],    # read-only monitoring dashboard (BUILD_MONIT)
    # The mail transport (BUILD_JESS). Flag-gated rather than core because it queues
    # every send: a host with no celery worker would have an email service whose
    # messages can never leave. Such a host keeps reading EMAIL_* from its environment.
    "jess": ["toto.jess"],
    # Univer spreadsheets, vault-backed (BUILD_PRIMULA). Flag-gated because it vendors a
    # large JS bundle that only the hosts offering sheets need baked into their image.
    "primula": ["toto.primula"],
    # The SVG editor (BUILD_SKETCH). Flag-gated like the other editors, with one
    # extra rule enforced by a system check in toto.sketch.apps: a host that sets
    # this MUST also set BUILD_ANTIVIRUS. Sketch renders SVG inline, in our
    # origin, and it has no screening of its own any more.
    "sketch": ["toto.sketch"],
    # Version control, in TOTO-REPO — two apps, and a host installs whichever
    # half it can actually use.
    #
    # "repo" (BUILD_REPO) is local git over vault directories: init, commit,
    # branch, merge, history, restore, and push to any URL. It carries the FK
    # into workflows.WorkflowRun, which is why toto.features forces the engine
    # on with it and why toto-repo depends on toto-flow.
    #
    # "gitea" (BUILD_GITEA) is the co-deployed forge: per-user accounts, a
    # repository list, and the credentials the local half asks for when a remote
    # belongs to it. No workflow engine, no celery — just the sidecar, which a
    # consumer host cannot run at all (deploy.py refuses services.gitea there).
    #
    # They were one app, toto.gitvault, until the hosts diverged: zenobia hosts
    # code and versions documents through toto.vault, placidia versions
    # workspaces on its own disk and mounts no OIDC provider to sign a sidecar
    # in with. One flag could not say that.
    "repo": ["toto.repo"],
    "gitea": ["toto.gitea"],
}


# Celery task modules for explicit autodiscovery (portal celery_app list,
# minus the long-dangling "toto.bazaar" whose app left the tree).
# "toto.transcription" left in 1.21 for toto-media-ops; nothing in toto-media has a
# celery task at all — that is the line between the two packages. A host that pins
# the ops wheel and installs transcription must add its label back here, or its jobs
# are enqueued and never discovered.
#
# "toto.manta" IS listed, even though no host pins the wheel yet: the entry is inert
# where the package is absent (Celery's find_related_module swallows a missing
# package), and leaving it out is the bug it prevents. Note it only works alongside
# manta/tasks.py — autodiscovery imports "<label>.tasks" and manta's task lives in
# tasks_direct, so without that module the worker never registers it.
TASK_MODULES = [
    "toto.workflows",
    # toto.weather's auto_refresh_weather. schedules.beat_schedule(weather=True)
    # has enqueued it every 30 minutes since the realtime layer existed, and
    # this entry was missing the whole time — so the worker answered KeyError
    # twice an hour and discarded the job, the identical failure the monit note
    # below describes. Inert where the app is not installed.
    "toto.weather",
    "toto.vault",       # encrypt_workflow_run (vault-encrypt workflow)
    "toto.mandragora",
    "toto.ravioli",
    "toto.connectors",
    "toto.formica",
    "toto.manta",       # toto-media-ops; needs manta/tasks.py to be discoverable
    # fileservices' direct task (run_file_service_task) — the extended-runtime
    # dispatch path and the no-workflow fallback both enqueue it, and without
    # this entry the worker answers KeyError and discards the job (the exact
    # transcription failure mode above). Inert where the ops wheel is absent.
    "toto.fileservices",
    "toto.jess",        # the mail queue — every email in the platform passes through it
    "toto.clearing",    # the ledger bridge: outbox delivery, redrive, hold expiry
    "toto.tax",         # the daily levy sweep; inert where toto-economy is absent
    # toto.quota's stuck-run sweeper (quota/tasks.py). In toto-base, so it is
    # importable on every host; the beat entry (schedules.beat_schedule
    # sweep=...) is what turns it on.
    "toto.quota",
    # toto.monit's sampler and pruner are SCHEDULED by toto/schedules.py whenever
    # BUILD_MONIT is on, so the worker has to be able to find them. Without the
    # entry beat kept enqueueing `toto.monit.tasks.monit_prune` and the worker
    # kept answering KeyError, once an hour, forever — a stack trace in the log
    # that looks like a broken worker and buries the ones that matter.
    #
    # Inert where the ops wheel is absent: Celery's find_related_module swallows
    # a missing package, which is the same reasoning as toto.manta above.
    "toto.monit",
    # toto.repo's run_git_task — the no-workflow fallback path in dispatch.py
    # enqueues it directly, so a host with celery but without the seeded
    # repo-run workflow still runs its init/push/pull. Inert where the app is
    # not installed. No toto.gitea entry: that half has no celery task at all,
    # which is the same line that keeps it off the workflows closure.
    "toto.repo",
]


def has_app(name: str) -> bool:
    """Capability check: is the given app (e.g. "toto.forum") installed?"""
    from django.apps import apps

    return apps.is_installed(name)
