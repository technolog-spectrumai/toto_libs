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
    # The media section, as of 1.21: two independent apps, both in toto-media, both
    # cheap. There is no "media" key any more — the processing stack it named
    # (manta + fileservices + transcription, and the ffmpeg layer under them) is
    # parked in toto_libs/limbo/. BUILD_MEDIA still resolves, as the umbrella
    # default for "vod"; see toto.features.
    "vod": ["toto.vod"],        # video-on-demand vault play plugin + library
    "graph": [
        "toto.ravioli",         # sole Neo4j boundary
        "toto.sql_neo4j_sync",  # SQL→Neo4j projection/sync layer
        "toto.neo_editor",      # dual-mode .neojson vault editor
        "toto.bento",           # first-class Neo4j graph editor
        "toto.ingestor",        # text → Bento-validated graph patch
    ],
    # toto.ocr moved from toto-graph to toto-media in 1.21 and no longer implies
    # the graph — its ingestor handoff greys itself out. BUILD_OCR is opt-in and
    # brings the tesseract apt layer with it.
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
# "toto.transcription" and "toto.manta" left in 1.21 with their apps; nothing in
# toto-media has a celery task any more.
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
