"""Effective feature resolution for toto deployments.

Single source of truth for the BUILD_* / INSTALL_* flag logic that was
previously duplicated between the portal host settings and the deploy
tooling (portal/portal/settings.py and portal/scripts/deploy.py).  An
explicit BUILD_<FEATURE> value overrides its tier; tiers are just defaults.

The realtime tier was called ``studio`` until ``studio`` became the name of a
host.  ``BUILD_STUDIO`` is still read as a fallback and ``Features.studio``
still resolves, so a host that has not been updated behaves identically.

Since 1.21 the media flags split across two packages. ``BUILD_MEDIA`` and
``BUILD_VOD``, plus ``BUILD_OCR`` and ``INSTALL_TESSERACT``, resolve to apps in
``toto-media`` — the light wheel hosts pin. ``BUILD_MANTA``, ``BUILD_FILESERVICES``
and ``INSTALL_FFMPEG`` resolve to ``toto-media-ops``, which **no host pins** (see
that package's README); they are honoured so pinning it needs no library change.
All of them are opt-in except ``BUILD_VOD``, which defaults from ``BUILD_MEDIA``.
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
    sketch: bool
    canasta: bool
    media: bool
    vod: bool
    # The toto-media-ops tier — the apps that want a celery worker. No host pins that
    # package, so these are off on every shipped profile; the flags exist so a host
    # that does pin it needs no library change. See that package's README.
    manta: bool
    fileservices: bool
    # Graph-group features (graph defaults to the neo4j tier). ocr is grouped here
    # for continuity only: since 1.21 it is opt-in, implies no Neo4j at all, and
    # ships in toto-media beside vod — see the media block in resolve_features.
    graph: bool
    ocr: bool
    connectors: bool
    formica: bool
    # Standalone features (opt-in only).
    steven: bool
    sabbia: bool
    travels: bool
    gitvault: bool
    primula: bool
    monit: bool
    jess: bool          # toto.jess — the mail transport and outbox
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
    # Native binaries the image needs (deploy-side). ffmpeg follows the toto-media-ops
    # apps, which no host pins; tesseract follows ocr, which ships in a wheel hosts DO
    # pin but is opt-in and switched off everywhere. So both resolve False on every
    # shipped profile and neither apt layer is declared in a host Dockerfile today.
    tesseract: bool
    ffmpeg: bool
    texlive: bool
    weasyprint: bool

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
    # latex, sketch, canasta, travels and gitvault are host-owned apps (see
    # the suite README): the flags stay here because they are part of the host
    # contract — needs_channels depends on sketch and canasta, `editor` defaults
    # from latex, and the workflows closure on latex/gitvault — but
    # registry.FEATURE_APPS deliberately has no entry for them, since the host
    # supplies the INSTALLED_APPS line from its own portion. `texlive` used to
    # default from latex too and no longer does; see where it is resolved below.
    sketch = flag(get, "BUILD_SKETCH")                        # toto.sketch — collaborative whiteboard
    # toto.canasta lives in zenobia's own portion (zenobia/toto/canasta). Its
    # table is a websocket, so it belongs in the needs_channels closure below —
    # and through it in `realtime`, which is what decides whether the image
    # installs requirements.realtime.txt at all. Without this line a host can set
    # BUILD_CANASTA=1, have settings name daphne/channels in INSTALLED_APPS, and
    # still get an image with neither installed: the container then dies on
    # ModuleNotFoundError from a config that looks entirely correct.
    canasta = flag(get, "BUILD_CANASTA")                      # toto.canasta — four-player team Canasta
    # BUILD_MEDIA is the media *tier*: an umbrella default, not an app switch. It is
    # an INPUT only — it must never be OR-ed back out of the per-app flags below, or
    # naming one app would silently enable the others.
    #
    # It used to cover four apps in one package. Since 1.21 the three that want a
    # celery worker live in toto-media-ops, which no host pins, and BUILD_MEDIA covers
    # only playback. Six shipped profiles set it and nothing else, which is why it
    # survives as the default for BUILD_VOD rather than being deleted; new configs
    # should name BUILD_VOD directly.
    #
    # It does NOT default BUILD_OCR, even though ocr is its package-mate: a profile
    # that asked for video must not silently acquire a tesseract apt layer.
    media = flag(get, "BUILD_MEDIA")
    # toto.vod (toto-media): no models (it dropped them in migration 0002), no
    # celery task, no native binary — an HTML5 player pointed at a vault file, plus
    # a library listing. Cheap enough for a lean WSGI host.
    vod = flag(get, "BUILD_VOD", media)

    # The toto-media-ops tier: opt-in only, and NOT defaulted from BUILD_MEDIA —
    # they are in a different package, so defaulting them from the tier every
    # profile sets would ask hosts to install a wheel they do not pin. Each is
    # independent of the others; see that package's README for what a host would
    # have to add first (native binaries, whisper wheels, a celery worker).
    manta = flag(get, "BUILD_MANTA")          # ffmpeg/ffprobe command builder
    fileservices = flag(get, "BUILD_FILESERVICES")   # the run substrate + vault wand

    # Graph-group features (default to the neo4j tier).
    graph = flag(get, "BUILD_GRAPH", tier_neo4j)              # ravioli + sql_neo4j_sync + neo_editor + bento + ingestor
    # toto.ocr — screenshot → tesseract → (optionally) the ingestor. Opt-in on its own
    # since 1.21, and no longer defaulted from the neo4j tier: it left toto-graph for
    # toto-media, beside vod, because it is light in every way that matters — no
    # models, no celery task, imports inside the function — and its only cost is a
    # small apt layer INSTALL_TESSERACT gates. The graph is an optional sink for its
    # text, not a requirement, hence no ocr → graph closure either; see ocr.views.
    ocr = flag(get, "BUILD_OCR")
    connectors = flag(get, "BUILD_CONNECTORS")                # toto.connectors — external-API ETL (opt-in)
    formica = flag(get, "BUILD_FORMICA")                      # toto.formica — colony curating the graph (opt-in)

    # Standalone features (no tier; opt-in only).
    steven = flag(get, "BUILD_STEVEN")                        # floating chat-widget UI (implies sabbia)
    sabbia = steven or flag(get, "BUILD_SABBIA")              # headless chat-agent backend (WebSocket)
    travels = flag(get, "BUILD_TRAVELS")                      # toto.travels — travel & visit log
    gitvault = flag(get, "BUILD_GITVAULT")                    # toto.gitvault — git repos over vault dirs
    primula = flag(get, "BUILD_PRIMULA")                      # toto.primula — Univer spreadsheets (vault-backed)
    # Lightweight read-only monitoring dashboard (grafana alternative). No
    # closure: the live panel works everywhere; snapshot HISTORY needs the
    # celery worker+beat stack, which the profiles enabling this already run.
    monit = flag(get, "BUILD_MONIT")                          # toto.monit — monitoring dashboard
    # toto.jess — email config in the database, sends queued through celery. Opt-in:
    # it makes EMAIL_BACKEND meaningless unless the host also points that at Jess,
    # and its sends need a worker.
    jess = flag(get, "BUILD_JESS")

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
    # weather, fileservices, latex (texlab) and gitvault all
    # have a model FK to workflows.WorkflowRun, so they require the workflows app —
    # else Django's system check fails with fields.E300/E307.
    #
    # Neither app still in toto-media appears here, which is new in 1.21: vod has no
    # models at all, and ocr's last workflows edge was a migration dependency left
    # over from a deleted model, cut by squashing its migrations. So BUILD_MEDIA no
    # longer reaches workflows, and through it celery — the reason zenobia_mini
    # could not be lean.
    #
    # manta is NOT here either, though it is in the same package as fileservices: it
    # has exactly one FK (FileJob.owner → User) and names workflows nowhere. What it
    # needs is celery, which the realtime tier below installs.
    if weather or fileservices or latex or gitvault:
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
    # toto.editor — the shared ACE base. Settable on its own, because it carries
    # EIGHT file-type plugins (text/json/yaml/xml/csv/html/latex/bib) and only two
    # of them belong to latex. Derived from latex alone, a host that
    # moved LaTeX elsewhere silently lost every vault Edit link:
    # vault/views.py renders "" for a file type with no plugin.
    editor = flag(get, "BUILD_EDITOR", latex)
    # Channels/ASGI back every WebSocket consumer.
    needs_channels = chat or latex or sketch or sabbia or canasta
    # Ollama/Qwen service layer — scoped to the features that actually use it.
    vicuna = graph or sabbia_ollama

    # Effective tier booleans (image pip layers + back-compat module attributes).
    #
    # vod and ocr are deliberately absent: vod is a template and a queryset, and ocr
    # runs tesseract synchronously inside the request. So BUILD_MEDIA=1 no longer
    # implies the celery/channels pip layer at all — which is what makes it viable
    # on a lean WSGI host for the first time.
    #
    # manta IS here, rather than in the workflows closure above, because what it
    # needs is celery and celery is what this tier installs
    # (requirements.realtime.txt). manta/tasks_direct.py does
    # `from celery import shared_task` at module scope and views.py calls
    # run_direct_job.delay(), so a BUILD_MANTA=1 image without this layer would not
    # boot. Note this buys the pip layer only: whether a worker container runs is
    # still services.celery in the profile, and manta jobs queue forever without one.
    #
    # jess is here for the same reason as manta, one step further along: jess/tasks.py
    # does `from celery import shared_task` at module scope AND its EMAIL_BACKEND calls
    # .delay() on the request path, so a BUILD_JESS=1 image without this layer would not
    # boot. It does not join the workflows closure — it has no FK to WorkflowRun and
    # dispatches its own task. Whether a worker container actually runs is still
    # services.celery in the profile; without one, mail queues and never leaves.
    realtime = chat or workflows or weather or needs_channels or manta or jess
    neo4j = graph

    # Native binaries, each following the feature that shells out to it. tesseract
    # and ffmpeg both belong to toto-media-ops apps, so on every shipped profile
    # both are False and no host Dockerfile declares either layer.
    #
    # INSTALL_TESSERACT used to pull ffmpeg in too (the old "OCR/media binaries"
    # bundle). That bundling is gone: ocr and the ffmpeg apps are separate features
    # and one is not a reason to ship the other's binary.
    explicit_tess = flag(get, "INSTALL_TESSERACT")
    explicit_ffmpeg = flag(get, "INSTALL_FFMPEG")
    tesseract = ocr or explicit_tess
    ffmpeg = fileservices or manta or explicit_ffmpeg
    # texlive (pdflatex) has TWO independent consumers: texlab compilation and
    # notarius contract→PDF export, which is a separate implementation sharing no
    # import with texlab and installed unconditionally. So it must NOT default to
    # either one — deriving it from `latex` meant a host that moved texlab away
    # silently lost notarius PDF export, and there was nothing to catch that.
    # Every profile states it; test_latex_profiles_state_whether_they_want_texlive
    # in the monorepo suite is the enforcement.
    texlive = flag(get, "INSTALL_TEXLIVE")
    # WeasyPrint (HTML→PDF) is a PIP layer, not an apt one: its native libraries
    # (cairo/pango/gdk-pixbuf/libffi) already ship in every host's base image, so all
    # that is gated is the wheel and the feature. Its own explicit flag, like texlive:
    # notarius contract→PDF is the first consumer and the invoice generator is the
    # planned second, so it must not derive from either.
    weasyprint = flag(get, "BUILD_WEASYPRINT")

    return Features(
        chat=chat,
        workflows=workflows,
        weather=weather,
        latex=latex,
        sketch=sketch,
        canasta=canasta,
        media=media,
        vod=vod,
        manta=manta,
        fileservices=fileservices,
        graph=graph,
        ocr=ocr,
        connectors=connectors,
        formica=formica,
        monit=monit,
        jess=jess,
        steven=steven,
        sabbia=sabbia,
        travels=travels,
        gitvault=gitvault,
        primula=primula,
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
        ffmpeg=ffmpeg,
        texlive=texlive,
        weasyprint=weasyprint,
    )
