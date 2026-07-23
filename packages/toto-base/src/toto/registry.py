"""App lists and capability checks for host projects.

Hosts compose INSTALLED_APPS from these lists (interleaving the Django and
third-party apps they need); the lists preserve the exact contents and order
the portal host has always used.
"""

# toto apps every host installs (portal settings base block, original order).
BASE_APPS = [
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
    "toto.sso_core",
    "toto.sso_master",
]

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
    "media": [                  # BUILD_MEDIA — the video/media stack
        "toto.manta",           # media/transcribe command builder
        "toto.transcription",   # Whisper audio/video transcription
        "toto.vod",             # video-on-demand vault play plugin
        "toto.fileservices",    # ffmpeg/ffprobe run services
    ],
    "graph": [
        "toto.ravioli",         # sole Neo4j boundary
        "toto.sql_neo4j_sync",  # SQL→Neo4j projection/sync layer
        "toto.neo_editor",      # dual-mode .neojson vault editor
        "toto.bento",           # first-class Neo4j graph editor
        "toto.ingestor",        # text → Bento-validated graph patch
    ],
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
TASK_MODULES = [
    "toto.workflows",
    "toto.vault",       # encrypt_workflow_run (vault-encrypt workflow)
    "toto.mandragora",
    "toto.ravioli",
    "toto.transcription",
    "toto.manta",
    "toto.connectors",
    "toto.formica",
]


def has_app(name: str) -> bool:
    """Capability check: is the given app (e.g. "toto.forum") installed?"""
    from django.apps import apps

    return apps.is_installed(name)
