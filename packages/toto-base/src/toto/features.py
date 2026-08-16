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
    steven_ai: bool
    sabbia: bool
    travels: bool
    # Version control, two halves that go to different hosts. repo is local git
    # over vault directories; gitea is the hosted forge and its per-user
    # accounts. Neither implies the other — see toto-repo's README.
    repo: bool
    gitea: bool
    mail: bool
    subscriptions: bool
    monit: bool
    jess: bool          # toto.jess — the mail transport and outbox
    # GIS. When off, locations loads without GeoDjango (no GDAL/GEOS/PostGIS) and
    # Address carries plain lat/lon floats — a much lighter host. Default on.
    geo: bool
    # Task boards. Subtractive like geo: a long-standing core app, so it stays on
    # unless a host says otherwise, and turning it off only drops toto.kanban.
    # Nothing has a model FK into it, so it leaves nothing dangling.
    kanban: bool
    # The zenobia workspace labs (host-owned apps, resolved here since 1.47 so
    # their closures cannot be forgotten: a Python lab whose image lacks the
    # jupyter packages, or a TeX lab whose image lacks pdflatex, is a lab in
    # name only). BUILD_AMBROSIA is the pre-split alias enabling both.
    antaresia: bool
    texlab: bool
    # Derived.
    editor: bool
    antivirus: bool
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
    # COMPULSORY since 8/2026. Workflows is the platform's job runner, and the
    # antivirus — which every content door depends on — queues its scans
    # through it. A flag that can switch off the machinery security rides on is
    # not a flag, it is a foot-gun; BUILD_WORKFLOWS is deliberately not read
    # any more, so no config, old or new, can turn this off. Note what this
    # implies one line down: workflows sits in the realtime-or chain, so the
    # realtime pip layer (celery and friends) is now part of every build too.
    workflows = True
    weather = flag(get, "BUILD_WEATHER", tier_realtime)       # toto.weather (FKs workflows.WorkflowRun)

    # Editing features (standalone — each enabled on its own; no labs tier).
    # canasta, travels, antaresia and texlab are host-owned apps (see
    # the suite README): the flags stay here because they are part of the host
    # contract — needs_channels depends on canasta, the workflows
    # closure on repo and texlab, realtime on antaresia — but
    # registry.FEATURE_APPS deliberately has no entry for them, since the host
    # supplies the INSTALLED_APPS line from its own portion. (BUILD_LATEX left
    # in 1.46 with the workspace split; the labs joined here in 1.47 so their
    # closures hold for ANY config, not just builder-written ones. gitvault was
    # in that list until it moved into a wheel and then split into repo+gitea,
    # both of which FEATURE_APPS now names like any other packaged app.)


    _ambrosia = flag(get, "BUILD_AMBROSIA")
    antaresia = flag(get, "BUILD_ANTARESIA", _ambrosia)       # zenobia/toto/antaresia — Python lab
    texlab = flag(get, "BUILD_TEXLAB", _ambrosia)             # zenobia/toto/texlab — TeX lab
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
    # The assistant, rewritten in 1.51 and DELIBERATELY not wired to the two
    # flags below it. `steven_ai` installs toto.steven, which queues work on a
    # celery worker and never opens a socket; `sabbia` is the older headless
    # chat backend, and it implies needs_channels — so tying them together would
    # drag daphne, channels_redis and the whole realtime pip layer onto every
    # host that wanted an assistant. They are separate features that happen to
    # live in the same wheel.
    steven_ai = flag(get, "BUILD_STEVEN_AI")                  # toto.steven — the selection assistant (worker-backed)
    steven = flag(get, "BUILD_STEVEN")                        # LEGACY: the retired chat-widget UI (implies sabbia)
    sabbia = steven or flag(get, "BUILD_SABBIA")              # headless chat-agent backend (WebSocket)
    travels = flag(get, "BUILD_TRAVELS")                      # toto.travels — travel & visit log
    repo = flag(get, "BUILD_REPO")                            # toto.repo — git repos over vault dirs
    # toto.gitea — the co-deployed forge's accounts and repository list. Only
    # the flag lives here; the sidecar itself is services.gitea in the deploy
    # config, and deploy.py refuses that pair on a consumer host.
    gitea = flag(get, "BUILD_GITEA")
    mail = flag(get, "BUILD_MAIL")
    subscriptions = flag(get, "BUILD_SUBSCRIPTIONS")          # toto.subscriptions — plans, entitlements and the monthly charge
    # Lightweight read-only monitoring dashboard (grafana alternative). No
    # closure: the live panel works everywhere; snapshot HISTORY needs the
    # celery worker+beat stack, which the profiles enabling this already run.
    monit = flag(get, "BUILD_MONIT")                          # toto.monit — monitoring dashboard
    # toto.jess — email config in the database, sends queued through celery. Opt-in:
    # it makes EMAIL_BACKEND meaningless unless the host also points that at Jess,
    # and its sends need a worker.
    jess = flag(get, "BUILD_JESS")

    # Mail is a mailbox app over jess's transport: it sends through jess so
    # there is ONE delivery log, and its system mailbox is sealed in jess's
    # strongbox. Asking for Mail without jess would be asking for a product
    # with no way to send, so the closure turns jess on rather than letting
    # the combination fail at import time.
    if mail:
        jess = True

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
    # weather, fileservices and repo all have a model FK to
    # workflows.WorkflowRun, so they require the workflows app —
    # else Django's system check fails with fields.E300/E307.
    #
    # gitea is NOT here, and the difference between the two halves of the old
    # gitvault flag is exactly that FK: GitRun lives with the LOCAL half, so a
    # host that only hosts code in Gitea needs no workflow engine, no celery
    # and no kernel image to do it.
    # texlab is here for a dispatch edge, not an FK: every compile runs as a
    # workflow (`ambrosia-compile-latex`), so without the engine the Compile
    # button can only refuse.
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
    if weather or fileservices or repo or texlab:
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
    # toto.editor — the shared ACE base, carrying EIGHT file-type plugins
    # (text/json/yaml/xml/csv/html/latex/bib). Explicit-only since 1.46: it used
    # to default from BUILD_LATEX, and that flag left with the workspace split —
    # the hosts that want the editors (all of them, today) say BUILD_EDITOR=1.
    editor = flag(get, "BUILD_EDITOR")
    # toto.antivirus — screens file content at the doors and on demand.
    antivirus = flag(get, "BUILD_ANTIVIRUS")
    # Channels/ASGI back every WebSocket consumer.
    needs_channels = chat or sabbia or canasta
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
    # antaresia is here for its pip layer, not a websocket: jupyter_client and
    # ipykernel ride requirements.realtime.txt, and a Python lab without them
    # boots fine and then fails on the first Run click.
    realtime = chat or workflows or weather or needs_channels or manta or jess or antaresia
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
    # texlive (pdflatex) has exactly ONE consumer since 1.47: TeX Lab
    # compilation. (notarius went WeasyPrint in 1.44 and signature-only in the
    # rework — no pdflatex anywhere else.) So it now DERIVES from texlab: a TeX
    # workspace whose image lacks the compiler is a lab in name only. An
    # explicit INSTALL_TEXLIVE=0 still wins, for the deliberate edit-only host.
    texlive = flag(get, "INSTALL_TEXLIVE", texlab)
    # WeasyPrint (HTML→PDF) is a PIP layer, not an apt one: its native libraries
    # (cairo/pango/gdk-pixbuf/libffi) already ship in every host's base image, so all
    # that is gated is the wheel and the feature. Its own explicit flag: the
    # cyprian/memo PDF exports are the consumers now (the reworked notarius
    # stamps with pypdf/reportlab and renders nothing), and the planned invoice
    # generator would be the next — it must not derive from any one of them.
    weasyprint = flag(get, "BUILD_WEASYPRINT")

    return Features(
        chat=chat,
        workflows=workflows,
        weather=weather,
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
        steven_ai=steven_ai,
        sabbia=sabbia,
        travels=travels,
        repo=repo,
        gitea=gitea,
        mail=mail,
        subscriptions=subscriptions,
        geo=geo,
        kanban=kanban,
        antaresia=antaresia,
        texlab=texlab,
        editor=editor,
        antivirus=antivirus,
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
