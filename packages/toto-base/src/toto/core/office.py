"""Office — one place for the things you make.

Documents, presentations, sheets and drawings are each a single self-contained
vault file edited by its own app, and until now each app was its own island:
memo and primula had a flat list apiece, cyprian had none at all (its urls.py:
"Cyprian stopped being a destination when the editors moved to the zinnia
desktop app"), and the dashboard tile called "Documents" opened htmlview, which
lists HTML pages. Office is the shared destination those four never had.

An Office area existed once (0fc1f6ab) and was removed two days later
(758bfa6c) because "the editors moved to the zinnia desktop app, and a landing
page for the two things that stayed was a hop to nowhere". The editors came
back in 8/2026; what is different here is that each tab now carries a real
list, a folder panel and real actions, rather than being a menu.

## What this module may and may not do

**It imports none of the office apps.** They are named as STRINGS for
`apps.is_installed`, and everything type-specific is asked of the vault's
plugin registries: `VaultEditorPlugin.for_file_type(ft)` for "open to edit",
`VaultPlayPlugin.for_file_type(ft)` for "open to read". That is not tidiness —
`toto.primula` is a zenobia HOST portion and this module ships in the toto-base
wheel, and a wheel may not import a host portion. The registries are the socket
that makes a Sheets tab possible from here at all.

**Every route here is a GET.** `toto.subscriptions.gate` reads the entitlement
from `resolver_match.app_name`; `cyprian`, `memo` and `primula` are Professional
while an app the catalogue does not know is free — so an Office-owned write
route would be a way to create paid content for nothing. Creating, renaming and
deleting are posted to the OWNING app or to the vault, whose gating already
decides them. `tests_office.py` asserts it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Section:
    """One Office tab.

    ``file_types`` is the whole of what separates one tab from another — the
    views are shared. Keeping the types, the label, the icon and the owning app
    in ONE row is deliberate: zinnia's tabs.ts makes the argument, that a
    listing filter and a creation default kept in different places drift until
    a tab writes a file it cannot itself reopen.
    """

    slug: str
    label: str
    icon: str
    #: Vault file types this tab lists. More than one where a type has a legacy
    #: spelling still in the wild.
    file_types: tuple[str, ...]
    #: Installed-app gate: the tab is offered when ANY of these is installed.
    #: More than one because a tab is a KIND OF THING, not an app — Documents
    #: holds both the writer's documents and the HTML pages the viewer renders,
    #: and it is worth showing if either app is here. Empty means the tab needs
    #: no app of its own: the vault holds the files and some other app may or
    #: may not open them.
    app_labels: tuple[str, ...] = ()
    #: Shown under the heading when the tab is empty.
    blurb: str = ""
    #: Said out loud when nothing on this host can edit the type.
    read_only_note: str = ""
    #: Show each row's type. True only where a tab mixes genuinely different
    #: kinds — NOT merely more than one `file_types` entry, since Presentations
    #: carries two spellings of one kind and would label every row twice over
    #: with the same word.
    show_type: bool = False
    #: An alternative listing of the same files, offered beside the tab —
    #: a url NAME, resolved lazily, dropped when it does not resolve. Exists
    #: for memo's cover gallery, which renders a real first slide per deck and
    #: cannot be reproduced here without toto.core importing toto.memo.
    alt_view: str = ""
    alt_label: str = ""
    #: The subscription entitlement writing this type needs, if any. Named as a
    #: plain string because this module may not import the apps: it is the same
    #: code `SubscriptionGateMiddleware` reads off the owning app's `app_name`,
    #: which is what makes asking here and enforcing there the same question.
    entitlement: str = ""


#: The tabs, in order. Documents first: it is the default tab and the one thing
#: this host could never list.
SECTIONS: tuple[Section, ...] = (
    Section(
        slug="documents", label="Documents", icon="fa-solid fa-file-lines",
        # ONE type, because there is now one thing. "A person looking for my
        # documents does not first decide whether the thing they wrote is a
        # cyprian document or an HTML page" was the argument for listing two
        # types here; retiring CTML on 2026-08-29 settled it the other way and
        # there is only the HTML page. The writer still exists — it is how a
        # kanban wiki page is written — but it owns no file type and claims
        # nothing in the vault.
        # `pdf` is listed but has no editor and no reader plugin, so
        # `open_url` falls through to the download door — which is right. It is
        # here so a PDF made from a page in this tab is VISIBLE in the tab it
        # was made from, instead of landing somewhere the person has to hunt.
        file_types=("html", "pdf"),
        app_labels=("toto.htmlview",),
        entitlement="htmlview", show_type=True,
        blurb="HTML documents — read them, or open them to edit and preview.",
    ),
    Section(
        slug="presentations", label="Presentations",
        icon="fa-solid fa-person-chalkboard",
        # BOTH spellings, exactly as memo.DECK_TYPES does. `presentation` is the
        # legacy string vault migration 0021 could not reach on mirrored, remote
        # and encrypted rows; a query that lists only `pxml` hides those decks
        # from their own owners without saying so.
        file_types=("pxml", "presentation"), app_labels=("toto.memo",),
        entitlement="memo", alt_view="memo:gallery", alt_label="Gallery view",
        blurb="Slideshows you build in the browser and present full-screen.",
    ),
    Section(
        slug="sheets", label="Sheets", icon="fa-solid fa-table-cells",
        file_types=("sheet",), app_labels=("toto.primula",),
        entitlement="primula",
        blurb="Spreadsheets, each one a file in your vault with a version kept on every save.",
    ),
    Section(
        slug="drawings", label="Drawings", icon="fa-solid fa-pen-ruler",
        # No app_labels: SVGs are ordinary vault files and nothing on this host
        # is required to own them. A host WITHOUT toto.sketch therefore still
        # lists and opens them — read-only, and `read_only_note` says so. A
        # host with it gets Edit and New, because the plugin claims `svg`.
        # This is the one tab that can exist with no editor behind it, which is
        # why the note is conditional rather than deleted.
        file_types=("svg",),
        entitlement="sketch",
        blurb="Diagrams and sketches, and any SVG you already have.",
        read_only_note=(
            "No drawing editor is installed on this server, so these open "
            "for reading only."
        ),
    ),
)

SECTIONS_BY_SLUG = {section.slug: section for section in SECTIONS}

#: How a list may be ordered. The value is what the queryset gets; the key is
#: what the URL carries, so a bookmarked sort keeps working if the ordering
#: expression ever changes.
SORTS = {
    "name": ("Name", "title"),
    "-name": ("Name (Z–A)", "-title"),
    "recent": ("Newest first", "-uploaded_at"),
    "oldest": ("Oldest first", "uploaded_at"),
    "largest": ("Largest first", "-file_size_bytes"),
    "smallest": ("Smallest first", "file_size_bytes"),
}
DEFAULT_SORT = "recent"


@dataclass(frozen=True)
class Tool:
    """Something you DO to a file, and a tab of its own.

    Still a separate dataclass from `Section`, because the two answer different
    questions — a Section is defined by the vault file types it LISTS and a
    tool lists none, so a tool has no rows, no folder panel, no New button and
    no sort. What changed on 2026-08-29 is only where it is rendered: tools
    were a row of small buttons beside the tab strip, which put "read a scan"
    and "turn this page into a PDF" somewhere people did not look. They are
    tabs now, in the same strip as Documents and Presentations, and
    `office_tabs()` is what merges the two kinds into one list.

    The comment that justified the old split said a tab strip must mean "kinds
    of thing you have". That reading lost: what the strip actually means to
    somebody using it is "the things Office does", and reading a scan is one
    of them.

    It remains a LINK, not a route — Office owns no write routes and a tool
    needs some, so each tool's own app keeps its entitlement and its POSTs.
    `url_name` is a NAME, resolved lazily and dropped on NoReverseMatch,
    exactly as `Section.alt_view` is, so an installed-but-unmounted app offers
    nothing rather than 500ing the whole Office page.
    """

    slug: str
    label: str
    icon: str
    url_name: str
    app_labels: tuple = ()
    blurb: str = ""
    entitlement: str = ""


TOOLS: tuple = (
    Tool(slug="ocr", label="Text recognition",
         icon="fa-solid fa-file-signature",
         url_name="ocr:home", app_labels=("toto.ocr",), entitlement="ocr",
         blurb="Turn a photo, a screenshot or a scanned PDF into text."),
    # Names a HOST-owned app, which this module may do: `app_labels` is a
    # string for `apps.is_installed` and `url_name` is resolved lazily, so
    # nothing here imports it. `Section.app_labels` already names toto.primula
    # the same way.
    Tool(slug="aralia", label="HTML to PDF", icon="fa-solid fa-file-pdf",
         url_name="aralia:editor", app_labels=("toto.aralia",),
         entitlement="aralia",
         blurb="Preview an HTML document, and render it to a PDF."),
)


def available_tools() -> list:
    """The tools this host both installs and mounts.

    Kept as its own function even though `office_tabs()` is what the page
    renders: a caller asking "does this host read scans?" should not have to
    filter a list of tabs to find out.
    """
    from django.apps import apps as django_apps
    from django.urls import NoReverseMatch, reverse

    out = []
    for tool in TOOLS:
        if tool.app_labels and not any(django_apps.is_installed(label)
                                       for label in tool.app_labels):
            continue
        try:
            url = reverse(tool.url_name)
        except NoReverseMatch:
            continue
        out.append({"slug": tool.slug, "label": tool.label, "icon": tool.icon,
                    "url": url, "blurb": tool.blurb})
    return out


def available_sections():
    """The tabs this host actually serves.

    `apps.is_installed` is not enough on its own — an app can be installed and
    mount no URL at all (zenobia keeps toto.mandragora installed purely for a
    foreign key). A tab whose app is absent is not rendered disabled; it is not
    rendered, because there is nothing behind it to explain.
    """
    from django.apps import apps as django_apps

    return [s for s in SECTIONS
            if not s.app_labels
            or any(django_apps.is_installed(label) for label in s.app_labels)]


def office_tabs(active: str = "") -> list:
    """THE tab strip — sections and tools, in one list, in one order.

    One builder because there is one strip, and a strip assembled half in this
    module and half in a template drifts: the Office page would order its tabs
    one way and the OCR page, which shows the same strip so the tab you clicked
    stays lit, would order them another.

    `active` is a SLUG rather than a URL. A tool's tab lives in another app
    entirely — `/ocr/`, `/aralia/` — so there is no request path Office could
    compare against, and the page that renders the strip is the only thing that
    knows which tab it is.

    TOOLS LEFT THIS STRIP on 2026-09-01 and the `active` slug is now always a
    section. They have a room of their own at /tools/, because the sentence the
    strip used to read — "here is what you have, then here is what you can do
    with it" — only held while a tool acted on something Office lists. Reading a
    scan does; turning arbitrary HTML into a PDF does not, and by then the
    strip was making a promise about its own contents that it could not keep.

    `active` is still a SLUG rather than a URL: `available_sections()` is
    filtered per host, so the page that renders the strip is the only thing
    that knows which tab it is.
    """
    from django.urls import NoReverseMatch, reverse

    tabs = []
    for section in available_sections():
        try:
            url = reverse("office:section", args=[section.slug])
        except NoReverseMatch:
            # Office itself unmounted. Nothing to point at, so nothing offered.
            continue
        tabs.append({"slug": section.slug, "label": section.label,
                     "icon": section.icon, "url": url, "blurb": section.blurb,
                     "is_tool": False, "active": section.slug == active})
    return tabs


def tools_tabs(active: str = "") -> list:
    """THE tools strip — the same shape Office's is, for the same reason.

    Tools were tabs in `office_tabs()` until 2026-09-01. They have a room of
    their own now, and a room with one door per tool and no way between them
    would be a worse version of what they left: the tool pages live in
    different apps (`/ocr/`, `/aralia/`), so without a shared strip, arriving at
    one drops you out of the set you picked it from. That is the exact
    complaint that made tools tabs in the first place.

    So the hub and every tool page render THIS, and one builder produces it —
    a tool cannot appear on the hub and be missing from its own page.

    `active` is a SLUG, not a URL, for the reason `office_tabs` gives: the page
    is in another app entirely, so there is no request path to compare against
    and only the page knows which tab it is.
    """
    return [{**tool, "active": tool["slug"] == active}
            for tool in available_tools()]


def files_for(user, section, *, search="", sort=DEFAULT_SORT, directory_id=None):
    """The files one tab lists, access-checked.

    Built on `accessible_files`, the queryset twin of `access.may_read`, for the
    reason primula's index gives: "the list and the page agree by construction —
    a sheet that appears here always opens, and one that opens always appears."
    Two hand-written filters would drift.
    """
    from django.db.models import Q

    from toto.vault.filetree import accessible_files

    qs = accessible_files(user, file_types=section.file_types)
    if directory_id:
        qs = qs.filter(directory_id=directory_id)
    if search:
        qs = qs.filter(Q(title__icontains=search) | Q(key__icontains=search))
    _label, ordering = SORTS.get(sort, SORTS[DEFAULT_SORT])
    # `pk` breaks ties so pagination cannot show one row on two pages, which is
    # what an unstable sort does to rows sharing a timestamp or a title.
    return qs.select_related("owner", "bucket", "directory").order_by(ordering, "pk")


def edit_url(user, vault_file) -> str:
    """Where this file goes to be CHANGED, or "" if this reader cannot.

    **Offered to the owner only** — the rule htmlview's own listing stated and
    the one a list has to apply: readable and writable are different questions,
    and a public document is readable by everyone and writable by nobody but
    its owner. `may_edit_via_app` is the one widening: it asks the type's
    `VaultAccessPlugin`, which is how a kanban wiki collaborator edits a
    document they do not own.

    Encrypted bytes and bytes that live on another host are excluded for the
    reasons the vault browser excludes them: an editor handed ciphertext shows
    a broken document, and a mirrored row is a copy of somebody else's file.
    """
    from django.urls import NoReverseMatch

    from toto.vault import access
    from toto.vault.plugins import VaultEditorPlugin

    if vault_file.is_encrypted or not access.is_local_content(vault_file):
        return ""
    owns = getattr(user, "pk", None) is not None and vault_file.owner_id == user.pk
    if not owns and not access.may_edit_via_app(user, vault_file):
        return ""
    plugin = VaultEditorPlugin.for_file_type(vault_file.file_type)
    if plugin is None:
        return ""
    try:
        return plugin.get_editor_url(vault_file) or ""
    except NoReverseMatch:
        # Installed but unmounted. No button beats a button that 404s.
        return ""


def open_url(vault_file):
    """Where this file goes to be LOOKED AT.

    The reader first, the editor only if nothing reads it. This ordering is
    the whole reason HTML pages belong here: `toto.htmlview` registers the
    PLAY plugin for `html` and its docstring names the split — "Edit opens the
    source, Play opens the rendering" — while the EDITOR plugin for the same
    type is cyprian's convert-or-fall-back-to-raw-source dispatch. Opening a
    document by converting it would be a strange thing for a list to do.

    A written document has no reader at all (cyprian's was removed with its
    library), so it falls through to the writer, which is how you read one.
    """
    from django.urls import NoReverseMatch

    from toto.vault.plugins import VaultPlayPlugin

    if vault_file.is_encrypted:
        # The same rule the vault browser applies: sealed bytes open nowhere,
        # and an editor handed ciphertext shows a broken document.
        return ""

    plugin = VaultPlayPlugin.for_file_type(vault_file.file_type)
    if plugin is not None:
        try:
            url = plugin.get_play_url(vault_file)
        except NoReverseMatch:
            url = ""
        if url:
            return url

    # No user here on purpose: this is the READ url, and whether the caller
    # could also write is a different question. The owner check lives in
    # `edit_url`, which the row asks separately.
    from toto.vault import access as _access
    from toto.vault.plugins import VaultEditorPlugin as _Editor

    if _access.is_local_content(vault_file):
        plugin = _Editor.for_file_type(vault_file.file_type)
        if plugin is not None:
            try:
                url = plugin.get_editor_url(vault_file)
            except NoReverseMatch:
                url = ""
            if url:
                return url

    # Nothing claims the type. Falling back to the vault's own download door is
    # what keeps the Drawings tab from being a list of things that do not open:
    # On a host without toto.sketch, `svg` has neither an editor plugin nor a play
    # plugin, and without this every row there would be inert. A download is a
    # poorer "open" than a viewer, which is why it is last and not first.
    return vault_file.get_public_url() or ""


def may_create(user, section) -> bool:
    """Whether this reader's plan covers writing this kind of thing.

    Asked once for the page rather than per row, and for the reason primula's
    index gives: "the form posts to a route the gate would 402 anyway, and
    offering a button that answers 'not in your plan' is worse than not
    offering one."
    """
    if not section.entitlement:
        return True
    from toto.subscriptions.gate import is_entitled

    return is_entitled(user, section.entitlement)


def type_is_editable(section) -> bool:
    """True when some editor plugin claims any of this tab's types.

    Office asks the registry rather than naming apps, for the reason this
    module's docstring gives. It exists so `Section.read_only_note` can be told
    honestly: on a host with no drawing editor the sentence is true and worth
    saying; on a host with one it is false and would contradict the Edit button
    sitting next to it.
    """
    from toto.vault.plugins import VaultEditorPlugin

    return any(VaultEditorPlugin.for_file_type(ft) is not None
               for ft in section.file_types)


def creatable_types(section, user=None):
    """The types this tab can seed a brand-new file of.

    Only a type whose editor plugin declares `new_file_extension` — the plugin
    is the only thing that knows what an empty workbook or an empty deck looks
    like, which is the whole reason `blank_content` exists on it. A tab with
    nothing creatable renders no New button rather than one that fails.

    With a ``user``, the plan is asked too: the button disappears rather than
    becoming a control that 402s on click.
    """
    from toto.vault.plugins import VaultEditorPlugin

    if user is not None and not may_create(user, section):
        return []
    out = []
    for file_type in section.file_types:
        plugin = VaultEditorPlugin.for_file_type(file_type)
        if plugin is not None and getattr(plugin, "new_file_extension", ""):
            out.append((file_type, plugin.new_file_extension))
    return out
