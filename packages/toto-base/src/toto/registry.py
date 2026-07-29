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
    "pyeditor": ["toto.antaresia"],
    "monit": ["toto.monit"],    # read-only monitoring dashboard (BUILD_MONIT)
}


# Celery task modules for explicit autodiscovery (portal celery_app list,
# minus the long-dangling "toto.bazaar" whose app left the tree).
# "toto.transcription" and "toto.manta" left in 1.21 for toto-media-ops, which no
# host pins; nothing in toto-media has a celery task at all now — that is the line
# between the two packages. A host that pins the ops wheel must add its labels back
# here, or its jobs are enqueued and never discovered.
TASK_MODULES = [
    "toto.workflows",
    "toto.vault",       # encrypt_workflow_run (vault-encrypt workflow)
    "toto.mandragora",
    "toto.ravioli",
    "toto.connectors",
    "toto.formica",
]


def has_app(name: str) -> bool:
    """Capability check: is the given app (e.g. "toto.forum") installed?"""
    from django.apps import apps

    return apps.is_installed(name)
