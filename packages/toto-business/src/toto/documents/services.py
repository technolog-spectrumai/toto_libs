"""Handing a built document to aralia, and reporting back.

One function. It exists so the three export views do not each repeat the
file-resolve-queue dance, and so there is exactly one place that knows the
Business Center renders through aralia rather than through anything of its own.

**The dependency is SOFT, and it has to be.** This wheel installs on any host;
`toto.aralia` is host-owned and not every host carries it. So every aralia
import lives behind `available()`, exactly the way
`toto_libs/limbo/hesperis/integration/ledger.py` treats this package's own ledger — a wheel
that hard-imported a host app would refuse to install anywhere else. Where
the renderer is absent, `export` refuses with a message instead of rendering.

**An export is a vault page first (2026-10-01).** Aralia renders an approved
source and nothing else — a vault page the requester may read, or a wiki page
(`toto.aralia.sources`) — and its `create_run` takes the resolved source. Raw
HTML handed to it is the door its rule closed, so the built document does not
go to aralia as HTML: it is filed as an HTML page in the requester's personal
bucket, folder "Business Center exports", and aralia resolves THAT page the
way it resolves any other. So an export gets what every company document
gets — the platform's document sanitiser, the federation's letterhead, the
provenance footer — and its PDF lands beside its page. The page stays when
the worker refuses: it can be rendered later from aralia's own page.

**The metering is aralia's** (`toto.aralia.billing`): quota and funds before
anything is filed, the charge only once the PDF is in the vault, keyed on the
run. A refused or failed export costs nothing and has nothing to refund.

The steps are aralia's `generate` view's, in its order; aralia's README names
this function, so a change there is made here too.
"""

from __future__ import annotations

from django.apps import apps
from django.utils.text import slugify
from django.utils.translation import gettext as _

# toto.quota ships in toto-base, which this package hard-depends on, so the
# refusals aralia's billing raises are named at module scope; only the ARALIA
# imports below are soft.
from toto.quota.api import InArrears, QuotaExceeded
from toto.quota.charge import InsufficientFunds

#: Where an export is filed: a folder in the requester's personal bucket, the
#: twin of aralia's "Wiki exports" and for its reason — the vault's access rule
#: lets the requester open it without a new ACL story. A stored folder name,
#: so not translated: a second language must not make a second folder.
EXPORT_FOLDER = "Business Center exports"


class ExportRefused(Exception):
    """The render could not be queued, and the message says why."""


def available() -> bool:
    """Is there a renderer on this host at all?"""
    return apps.is_installed("toto.aralia")


def queued_message() -> str:
    """What an export view says once the render is queued. It names the
    folder because none of their pages shows a render (2026-10-01)."""
    return _("The PDF is rendering. It will be in your vault, in the folder "
             "“%(folder)s”.") % {"folder": EXPORT_FOLDER}


def export(html: str, *, user, label: str = "export"):
    """Queue one Business Center export. Returns the AraliaRun.

    Quota, funds and the letterhead are checked BEFORE the page is filed, so a
    refusal leaves nothing behind; the page is then resolved through aralia's
    sources like any other, and the run is made from what came back.
    """
    if not available():
        raise ExportRefused(_(
            "This host has no PDF renderer — the Business Center exports "
            "through toto.aralia, which is not installed here."))
    from toto.aralia import billing, dispatch, katex, letterhead, sources
    from toto.aralia import provenance as prov_mod
    from toto.aralia.models import SourceKind
    from toto.aralia.templates_build import document_html

    if not (html or "").strip():
        raise ExportRefused(_("There is nothing to render."))
    data = html.encode("utf-8")
    if len(data) > sources.MAX_SOURCE_BYTES:
        raise ExportRefused(_("This document is too large to render."))

    try:
        billing.check_before_dispatch(user)
        head = letterhead.for_source(SourceKind.FILE)
    except (QuotaExceeded, InArrears, InsufficientFunds, letterhead.NoLogo) as exc:
        raise ExportRefused(str(exc)) from None

    page = file_page(data, user=user, label=label)
    try:
        source = sources.resolve(user, SourceKind.FILE, str(page.pk))
    except sources.SourceRefused as exc:
        raise ExportRefused(str(exc)) from None

    # The row first, then the provenance footer that names it — built by
    # aralia, escaped, after </main>; never substituted into the body.
    extra_head = katex.inline_if_needed(source.body_html)
    run = dispatch.create_run(
        html=document_html(head, source.body_html, "", extra_head=extra_head),
        user=user, source=source, letterhead=head)
    prov = prov_mod.build(run, platform_name=_platform_name())
    run.html = document_html(head, source.body_html, prov_mod.footer_html(prov),
                             extra_head=extra_head)
    run.save(update_fields=["html"])

    try:
        dispatch.dispatch_run(run)
    except dispatch.CannotQueue as exc:
        dispatch.fail_run(run, str(exc))
        raise ExportRefused(str(exc)) from None
    return run


def file_page(data: bytes, *, user, label: str = "export"):
    """The export as an HTML page in the requester's "Business Center exports"
    folder, named after `label`; a name already taken there gets `-2`."""
    from toto.aralia.vault_files import save_bytes
    from toto.vault.models import VaultDirectory, personal_bucket

    bucket = personal_bucket(user)
    directory, _created = VaultDirectory.objects.get_or_create(
        bucket=bucket, name=EXPORT_FOLDER, parent=None, defaults={"owner": user})
    stem = slugify(label)[:100].strip("-") or "export"
    return save_bytes(owner=user, data=data, filename=f"{stem}.html",
                      file_type="html", bucket=bucket, directory=directory)


def _platform_name() -> str:
    """The active platform's name, for the footer — what aralia's runner
    writes into the PDF's metadata, so the two agree."""
    from toto.core.models import Platform

    platform = Platform.objects.filter(active=True).first()
    return platform.site_name if platform else ""
