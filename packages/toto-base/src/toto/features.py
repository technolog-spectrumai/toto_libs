"""Effective feature resolution for toto deployments.

Single source of truth for the BUILD_* / INSTALL_* flag logic that was
previously duplicated between the portal host settings and the deploy
tooling (portal/portal/settings.py and portal/scripts/deploy.py).  An
explicit BUILD_<FEATURE> value overrides its tier; tiers are just defaults.

The realtime tier was called ``studio`` until ``studio`` became the name of a
host.  ``BUILD_STUDIO`` is still read as a fallback and ``Features.studio``
still resolves, so a host that has not been updated behaves identically.

Retired in 1.21: ``BUILD_MANTA``, ``BUILD_FILESERVICES`` and ``INSTALL_FFMPEG``,
with the apps behind them (see ``toto_libs/limbo/{manta,fileservices,
transcription}/PARKED.md``). Unlike ``BUILD_STUDIO`` these are not read as
fallbacks — a config still setting one gets nothing, silently, which is what a
retired flag should do. ``BUILD_MEDIA`` survives and now means video playback.
"""
from dataclasses import dataclass


class FeatureConfigError(ValueError):
    """A requested BUILD_* combination is contradictory (raised at build/startup)."""


@dataclass(frozen=True)
class Features:
    # Realtime-group features (default to the realtime tier).
    chat: bool
    workflows: bool
    weather: bool
    # Editing features (standalone - each enabled on its own; no labs tier).
    latex: bool
    pyeditor: bool
    sketch: bool
    media: bool
    vod: bool
    # Graph-group features (graph defaults to the neo4j tier). ocr is listed here
    # for continuity only: since 1.21 it is opt-in, implies no Neo4j, and lives in
    # toto-media beside vod.
    graph: bool
    ocr: bool
    connectors: bool
    formica: bool
    # Standalone features (opt-in only).
    steven: bool
    sabbia: bool
    travels: bool
    gitvault: bool
    monit: bool
    # GIS. When off, locations loads without GeoDjango (no GDAL/GEOS/PostGIS) and
    # Address carries plain lat/lon floats — a much lighter host. Default on.
    geo: bool
    # Task boards. Subtractive like geo: a long-standing core app, so it stays on
    # unless a host says otherwise, and turning it off only drops toto.kanban.
    # Nothing has a model FK into it, so it leaves nothing dangling.
    kanban: bool
    # Derived.
    editor: bool
    vicuna: bool
    sabbia_openai: bool
    sabbia_ollama: bool
    needs_channels: bool
    # Effective tiers (image pip layers / ENV).
    realtime: bool
    neo4j: bool
    # Native binaries the image needs (deploy-side). ``ffmpeg`` was here until
    # 1.21; its only two consumers (manta, fileservices) are parked in limbo and
    # nothing else in the tree shells out to it, so the field, INSTALL_FFMPEG and
    # the image's apt layer all went with them.
    tesseract: bool
    texlive: bool

    @property
    def studio(self) -> bool:
        """Deprecated alias for :attr:`realtime`.

        The tier was called ``studio`` until ``studio`` became the name of a
        HOST (see the portal monorepo's ``studio.md``). It never meant "the
        studio host" — it meant "this image needs celery and channels" — so it
        was renamed rather than left to collide.

        Kept because the sibling hosts vendor their own copy of this module at
        their own pins and read ``f.studio`` in their deploy tooling; this makes
        their next re-vendor a no-op. Do not remove without checking them.
        """
        return self.realtime


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
    # BUILD_STUDIO is the tier's old name, honoured so a host that has not been
    # updated still builds the same image. An explicit BUILD_REALTIME wins.
    tier_realtime = (flag(get, "BUILD_REALTIME") if get("BUILD_REALTIME") is not None
                     else flag(get, "BUILD_STUDIO"))
    tier_neo4j = flag(get, "BUILD_NEO4J")

    # Realtime-group features (default to the realtime tier).
    chat = flag(get, "BUILD_CHAT", tier_realtime)             # toto.forum — live chat (WebSocket)
    workflows = flag(get, "BUILD_WORKFLOWS", tier_realtime)   # toto.workflows + toto.mandragora kernel
    weather = flag(get, "BUILD_WEATHER", tier_realtime)       # toto.weather (FKs workflows.WorkflowRun)

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
    # BUILD_MEDIA is the media *tier*: a back-compat umbrella default, not an app
    # switch. It is an INPUT only — it must never be OR-ed back out of the per-app
    # flags below, or naming one app would silently enable the others.
    #
    # The media section was four apps until 1.21 and is two now. transcription,
    # manta and fileservices are parked in limbo/ (each with a PARKED.md saying
    # why); what is left is playback and OCR, both cheap and both independent:
    #
    #   BUILD_MEDIA=1                     video playback (what it now means)
    #   BUILD_MEDIA=0 BUILD_VOD=1         the same thing, said directly
    #   BUILD_MEDIA=0 BUILD_OCR=1         OCR alone: tesseract, no celery, no ffmpeg
    #
    # Kept rather than deleted because six shipped profiles set BUILD_MEDIA and
    # nothing else; without this they would silently lose video playback. New
    # configs should name BUILD_VOD. BUILD_MANTA and BUILD_FILESERVICES are no
    # longer read at all — a config still setting them gets no apps and no error,
    # which is the intended outcome for a retired feature.
    media = flag(get, "BUILD_MEDIA")
    # toto.vod: no models (it dropped them in migration 0002), no celery task, no
    # native binary — an HTML5 player pointed at a vault file, plus a library
    # listing. This is what lets a small host keep video playback for free.
    vod = flag(get, "BUILD_VOD", media)

    # Graph-group features (default to the neo4j tier).
    graph = flag(get, "BUILD_GRAPH", tier_neo4j)              # ravioli + sql_neo4j_sync + neo_editor + bento + ingestor
    # toto.ocr — screenshot → tesseract → (optionally) the ingestor. Opt-in on its
    # own since 1.21, and no longer defaulted from the neo4j tier: it moved out of
    # toto-graph into toto-media, where it belongs by shape (take a vault file,
    # shell out to a native binary). The graph is an optional sink for its text,
    # not a requirement — hence no ocr → graph closure either; see ocr.views.
    ocr = flag(get, "BUILD_OCR")
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

    # Task boards. Subtractive like geo, and for the same reason: it has always
    # been a core app, so a host that never names it keeps it. Set BUILD_KANBAN=0
    # to drop it. No closure entry — nothing else needs it. toto.locations reads
    # kanban models on the zone page, but guards that with apps.is_installed and
    # imports them inside the view, so a kanban-less host just shows the zone.
    kanban = flag(get, "BUILD_KANBAN", default=True)          # toto.kanban — project/task boards

    # Map-dependent apps cannot run without geometry — fail loud rather than
    # silently pulling GIS back in (the coordinate reads and map overlays in
    # weather/travels need it). Explicit per the build contract.
    if (weather or travels) and not geo:
        raise FeatureConfigError(
            "BUILD_WEATHER/BUILD_TRAVELS require BUILD_GEO=1 "
            "(map features need geometry); set BUILD_GEO=1 or disable them."
        )

    # Dependency closure — a feature pulls in what it cannot run without.
    # weather, latex (texlab), pyeditor (antaresia) and gitvault all have a model FK
    # to workflows.WorkflowRun, so they require the workflows app — else Django's
    # system check fails with fields.E300/E307.
    #
    # fileservices was in this list until 1.21 and was the strongest entry (a live
    # FK plus a module-scope import of workflows.predefined_tasks). It is parked, so
    # nothing in the media section forces workflows any more: vod has no models at
    # all, and ocr's last workflows edge was a migration dependency left over from a
    # deleted model, cut by squashing its migrations.
    if weather or latex or pyeditor or gitvault:
        workflows = True
    # connectors / formica feed or curate the ingestor → bento/ravioli graph.
    #
    # ocr is NOT in this list, as of 1.21. It reaches the graph through exactly one
    # optional button, and both halves of that handoff already degrade on their own:
    # ocr_home wraps reverse("ingestor:home") plus the ravioli import in
    # except (NoReverseMatch, ImportError) and greys the button out, and ocr_ingest
    # redirects back to itself on NoReverseMatch. The closure was costing five Neo4j
    # apps and an auto-started neo4j container (deploy.py's graph branch) for a
    # button that switches itself off.
    if connectors or formica:
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
    # Neither surviving media app appears here: vod is a template and a queryset,
    # and ocr runs tesseract synchronously inside the request. So the media section
    # no longer implies the celery/channels pip layer at all — which is what makes
    # BUILD_MEDIA=1 viable on a lean WSGI host for the first time.
    realtime = chat or workflows or weather or needs_channels
    neo4j = graph

    # Native binaries. Two left: tesseract follows ocr, texlive follows latex.
    # INSTALL_FFMPEG is gone as of 1.21 along with manta and fileservices, and with
    # it the old "OCR/media binaries" bundling where INSTALL_TESSERACT pulled ffmpeg
    # in as well. A config still setting INSTALL_FFMPEG is ignored, not an error.
    explicit_tess = flag(get, "INSTALL_TESSERACT")
    tesseract = ocr or explicit_tess
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
        vod=vod,
        graph=graph,
        ocr=ocr,
        connectors=connectors,
        formica=formica,
        monit=monit,
        steven=steven,
        sabbia=sabbia,
        travels=travels,
        gitvault=gitvault,
        geo=geo,
        kanban=kanban,
        editor=editor,
        vicuna=vicuna,
        sabbia_openai=sabbia_openai,
        sabbia_ollama=sabbia_ollama,
        needs_channels=needs_channels,
        realtime=realtime,
        neo4j=neo4j,
        tesseract=tesseract,
        texlive=texlive,
    )
