"""Effective feature resolution for toto deployments.

Single source of truth for the BUILD_* / INSTALL_* flag logic that was
previously duplicated between the portal host settings and the deploy
tooling (portal/portal/settings.py and portal/scripts/deploy.py).  An
explicit BUILD_<FEATURE> value overrides its tier; tiers are just defaults.
"""
from dataclasses import dataclass


class FeatureConfigError(ValueError):
    """A requested BUILD_* combination is contradictory (raised at build/startup)."""


@dataclass(frozen=True)
class Features:
    # Studio-group features (default to the studio tier).
    chat: bool
    workflows: bool
    weather: bool
    # Editing features (standalone - each enabled on its own; no labs tier).
    latex: bool
    pyeditor: bool
    sketch: bool
    media: bool
    fileservices: bool
    # Graph-group features (graph/ocr default to the neo4j tier).
    graph: bool
    ocr: bool
    connectors: bool
    formica: bool
    # Standalone features (opt-in only).
    manta: bool
    steven: bool
    sabbia: bool
    travels: bool
    gitvault: bool
    monit: bool
    # GIS. When off, locations loads without GeoDjango (no GDAL/GEOS/PostGIS) and
    # Address carries plain lat/lon floats — a much lighter host. Default on.
    geo: bool
    # Derived.
    editor: bool
    vicuna: bool
    sabbia_openai: bool
    sabbia_ollama: bool
    needs_channels: bool
    # Effective tiers (image pip layers / ENV).
    studio: bool
    neo4j: bool
    # Native binaries the image needs (deploy-side).
    tesseract: bool
    ffmpeg: bool
    texlive: bool


def flag(get, name, default=False):
    """Read a BUILD_*/INSTALL_* style flag. Explicit '0'/'1' wins; else `default`.

    ``get`` is a name -> raw-or-None accessor: ``os.environ.get`` in host
    settings, an env-config dict's ``.get`` in deploy tooling.  YAML configs
    may carry ints/bools, so values are compared as strings.
    """
    raw = get(name)
    if raw is None:
        return bool(default)
    return str(raw) == "1"


def resolve_features(get) -> Features:
    """Resolve coarse tiers + per-feature flags into effective build decisions."""
    tier_studio = flag(get, "BUILD_STUDIO")
    tier_neo4j = flag(get, "BUILD_NEO4J")

    # Studio-group features (default to the studio tier).
    chat = flag(get, "BUILD_CHAT", tier_studio)               # toto.forum — live chat (WebSocket)
    workflows = flag(get, "BUILD_WORKFLOWS", tier_studio)     # toto.workflows + toto.mandragora kernel
    weather = flag(get, "BUILD_WEATHER", tier_studio)         # toto.weather (FKs workflows.WorkflowRun)

    # Editing features (standalone — each enabled on its own; no labs tier).
    latex = flag(get, "BUILD_LATEX")                          # toto.texlab
    pyeditor = flag(get, "BUILD_PYEDITOR")                    # toto.antaresia — Python editor
    # latex, sketch, travels and gitvault are host-owned apps (see
    # the suite README): the flags stay here because they are part of the host
    # contract — needs_channels depends on sketch, `editor`/`texlive` on latex,
    # and the workflows closure on latex/gitvault — but registry.FEATURE_APPS
    # deliberately has no entry for them, since the host supplies the
    # INSTALLED_APPS line from its own portion.
    sketch = flag(get, "BUILD_SKETCH")                        # toto.sketch — collaborative whiteboard
    # BUILD_MEDIA — the video/media stack (manta + transcription + vod +
    # fileservices), all four now in the optional toto-media package. The old
    # per-app flags are honoured for back-compat, but BUILD_MEDIA is the one to
    # set. `manta` and `fileservices` remain as derived aliases so the closures
    # and every host settings read of _F.manta / _F.fileservices are unchanged.
    media = (flag(get, "BUILD_MEDIA")
             or flag(get, "BUILD_MANTA")
             or flag(get, "BUILD_FILESERVICES"))
    fileservices = media
    manta = media

    # Graph-group features (default to the neo4j tier).
    graph = flag(get, "BUILD_GRAPH", tier_neo4j)              # ravioli + sql_neo4j_sync + neo_editor + bento + ingestor
    ocr = flag(get, "BUILD_OCR", tier_neo4j)                  # toto.ocr — screenshot → tesseract → ingestor
    connectors = flag(get, "BUILD_CONNECTORS")                # toto.connectors — external-API ETL (opt-in)
    formica = flag(get, "BUILD_FORMICA")                      # toto.formica — colony curating the graph (opt-in)

    # Standalone features (no tier; opt-in only).
    steven = flag(get, "BUILD_STEVEN")                        # floating chat-widget UI (implies sabbia)
    sabbia = steven or flag(get, "BUILD_SABBIA")              # headless chat-agent backend (WebSocket)
    travels = flag(get, "BUILD_TRAVELS")                      # toto.travels — travel & visit log
    gitvault = flag(get, "BUILD_GITVAULT")                    # toto.gitvault — git repos over vault dirs
    # Lightweight read-only monitoring dashboard (grafana alternative). No
    # closure: the live panel works everywhere; snapshot HISTORY needs the
    # celery worker+beat stack, which the profiles enabling this already run.
    monit = flag(get, "BUILD_MONIT")                          # toto.monit — monitoring dashboard

    # GIS toggle. On by default (every legacy host has PostGIS). Set BUILD_GEO=0
    # for a light host: locations stays installed but geometry-less, no GDAL.
    geo = flag(get, "BUILD_GEO", default=True)                # django.contrib.gis + spatial DB

    # Map-dependent apps cannot run without geometry — fail loud rather than
    # silently pulling GIS back in (the coordinate reads and map overlays in
    # weather/travels need it). Explicit per the build contract.
    if (weather or travels) and not geo:
        raise FeatureConfigError(
            "BUILD_WEATHER/BUILD_TRAVELS require BUILD_GEO=1 "
            "(map features need geometry); set BUILD_GEO=1 or disable them."
        )

    # Dependency closure — a feature pulls in what it cannot run without.
    # weather, fileservices, manta, latex (texlab), pyeditor (antaresia)
    # and gitvault all have a model FK to workflows.WorkflowRun, so they require
    # the workflows app — else Django's system check fails with fields.E300/E307.
    if weather or fileservices or manta or latex or pyeditor or gitvault:
        workflows = True
    # ocr / connectors / formica all feed or curate the ingestor → bento/ravioli graph.
    if ocr or connectors or formica:
        graph = True

    sabbia_openai = sabbia and flag(get, "SABBIA_OPENAI")     # OpenAI creds + Steven agent
    sabbia_ollama = sabbia and flag(get, "SABBIA_OLLAMA")     # Ollama endpoint (vicuna)

    # Derived infrastructure.
    editor = latex or pyeditor                                # toto.editor — shared ACE editor base
    # Channels/ASGI back every WebSocket consumer.
    needs_channels = chat or latex or pyeditor or sketch or sabbia
    # Ollama/Qwen service layer — scoped to the features that actually use it.
    vicuna = graph or sabbia_ollama

    # Effective tier booleans (image pip layers + back-compat module attributes).
    studio = chat or workflows or weather or needs_channels
    neo4j = graph or ocr

    # Native binaries. INSTALL_TESSERACT historically also pulled ffmpeg in (the
    # "OCR/media binaries" bundle), so honour it for both for back-compat.
    explicit_tess = flag(get, "INSTALL_TESSERACT")
    explicit_ffmpeg = flag(get, "INSTALL_FFMPEG")
    tesseract = ocr or explicit_tess
    ffmpeg = fileservices or manta or explicit_tess or explicit_ffmpeg
    # texlive (pdflatex) backs latex compilation in texlab AND notarius
    # contract→PDF export. Defaults to the latex feature; an explicit
    # INSTALL_TEXLIVE wins.
    texlive = flag(get, "INSTALL_TEXLIVE", latex)

    return Features(
        chat=chat,
        workflows=workflows,
        weather=weather,
        latex=latex,
        pyeditor=pyeditor,
        sketch=sketch,
        media=media,
        fileservices=fileservices,
        graph=graph,
        ocr=ocr,
        connectors=connectors,
        formica=formica,
        manta=manta,
        monit=monit,
        steven=steven,
        sabbia=sabbia,
        travels=travels,
        gitvault=gitvault,
        geo=geo,
        editor=editor,
        vicuna=vicuna,
        sabbia_openai=sabbia_openai,
        sabbia_ollama=sabbia_ollama,
        needs_channels=needs_channels,
        studio=studio,
        neo4j=neo4j,
        tesseract=tesseract,
        ffmpeg=ffmpeg,
        texlive=texlive,
    )
