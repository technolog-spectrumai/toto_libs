from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Prefetch
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import translation
from django.utils.cache import add_never_cache_headers
from django.utils.translation import gettext_lazy as _
from django.views.generic import ListView, DetailView

from toto.core.safe_next import safe_next
from toto.people.models import Person
from toto.socialhub.models import Community
from toto.socialhub.plugins.profile_plugins import PLUGIN_TABS, ProfilePlugin
from toto.socialhub.views import account as own_account
from toto.ui import PageProcessor


def listed_communities(user):
    """The chips a profile shows ``user``: no clearance for a member (2026-09-28)
    — the directory's rule, so a chip never links to a page that 404s."""
    return Prefetch("communities",
                    queryset=Community.objects.all().order_by("name"),
                    to_attr="listed_communities")


class ProfileListView(LoginRequiredMixin, ListView):
    """The member roster.

    LoginRequiredMixin was missing, so this published every person on the
    platform — names, avatars, communities — to anyone who found the URL.
    `zenobia/tests/test_login_required.py` opens by naming this exact page as
    one of the two places the default-open mistake had already been made.
    """

    model = Person
    template_name = "socialhub/profile_list.html"
    context_object_name = "profiles"
    paginate_by = 10

    def get_queryset(self):
        # A stable order (2026-10-01, 37c.22): Person has none of its own, so
        # each page was a slice of whatever order the database chose that
        # time — a member could land on two pages or on none, and Django
        # warned on every request (UnorderedObjectListWarning).
        return (super().get_queryset()
                .order_by("display_name", "id")
                .prefetch_related(listed_communities(self.request.user)))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Each member's e-mail address only where they show it, or to
        # themselves or an administrator (2026-10-01, 37c.25): the roster
        # printed everybody's under their name.
        from toto.socialhub.contact_access import shown_email

        for member in context["profiles"]:
            member.shown_email = shown_email(self.request.user, member)
        return PageProcessor().decorate(context, self.request)


class ProfileDetailView(LoginRequiredMixin, DetailView):
    """A member's profile, in tabs (2026-10-02, stage 50) — ``?tab=``
    (`views/account.py`, ``TABS``).

    Its owner gets seven: Overview (the page others see, the default), Edit
    profile, Account, Security, Wallet, Activity and Your data. Anybody else
    — another member, staff, a superuser on the Superuser plan — gets the
    public three: Overview, Communities and Activity. A tab the viewer is not
    shown is the Overview, whatever the address asks for: no 403, and no part
    of somebody else's account is ever built.
    """

    model = Person
    template_name = "socialhub/profile_details.html"
    context_object_name = "profile"

    def get_queryset(self):
        return profile_queryset(self.request.user)

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        return render_profile(request, self.object, own=is_own(request.user, self.object),
                              tab=own_account.tab_from(request.GET))


def profile_queryset(viewer):
    """Persons as the profile page fetches them."""
    return (Person.objects.all()
            .prefetch_related(listed_communities(viewer))
            .select_related("user"))


def is_own(user, profile) -> bool:
    """Is ``profile`` the signed-in ``user``'s own? By the row's user id —
    being staff or a superuser makes nobody else's profile one's own."""
    return bool(user.is_authenticated and profile.user_id
                and profile.user_id == user.pk)


def own_tabs(request, profile):
    """The tabs of one's own page (`views/account.py`, ``OWN_TABS``). With no
    profile saved yet, only the account's, Edit profile first; with one,
    every tab but Wallet when no plugin there would show (a host without the
    economy). Asking costs no query today: the Activity tab always has the
    reference requests, so its plugins are not asked here."""
    if not profile.pk:
        return list(own_account.NO_PROFILE_TABS)
    return [tab for tab in own_account.OWN_TABS
            if tab != "wallet"
            or ProfilePlugin.shows_on_tab("wallet", request=request, profile=profile)]


def visitor_tabs(request, profile):
    """The tabs of somebody else's profile (``VISITOR_TABS``): the Overview,
    the Communities, and the Activity when one of its plugins shows to this
    viewer, by its own rules."""
    return [tab for tab in own_account.VISITOR_TABS
            if tab != "activity"
            or ProfilePlugin.shows_on_tab("activity", request=request, profile=profile)]


def _overview(request, profile, own):
    """The Overview: what may be seen of ``profile`` by the viewer — the page
    every member sees; the owner's own contact details marked where they are
    hidden from others (the template), and their communities with it."""
    # The e-mail address, the phone number and the postal address only
    # where their owner shows them, or to the owner or an administrator
    # (2026-10-01, 37c.25) — the rule the roster and the org-chart API ask
    # too. The postal address is text the member typed (2026-10-04); until
    # then it was a map pin under a resolver of its own.
    from toto.socialhub.contact_access import (may_see_address, may_see_email,
                                               may_see_phone)

    return {"may_see_email": may_see_email(request.user, profile),
            "may_see_phone": may_see_phone(request.user, profile),
            "may_see_address": may_see_address(request.user, profile)}


def _activity(request, profile, own):
    """The Activity tab: its plugins (the upcoming events, the recovery
    requests), and — on one's own profile — the reference requests to answer,
    between the two (``REFERENCES_ORDER``)."""
    if not own:
        return {"reference_requests": None}
    return {"reference_requests": profile.sent_references.select_related(
        "application", "application__community").order_by("-created_at")}


#: Where the reference requests sit among the Activity tab's plugin sections:
#: after those ordered below it (the upcoming events, 35), before the rest
#: (the password-recovery cards, 36, which copy their shape).
REFERENCES_ORDER = 36


def render_profile(request, profile, *, own, tab="", status=200, **forms):
    """Draw ``profile`` for ``request.user``, on ``tab`` (2026-10-02, stage 50).

    The tabs shown are the owner's (``own_tabs``) or a visitor's
    (``visitor_tabs``); any other ``tab`` is the first of them. Only the
    active tab is drawn and only its queries run. The owner's account tabs
    are built from ``request.user`` (`views/account.py`, ``tab_context``),
    never from the profile, and for nobody else. ``forms`` are a door's bound
    forms, drawn again with their errors. Links on the page are absolute
    (``page_url``): a form drawn again answers at its door's address, where a
    relative ``?tab=`` would be a GET on the door.
    """
    if own:
        shown = own_tabs(request, profile)
        page_url = own_account.page_url_of(profile)
    else:
        shown = visitor_tabs(request, profile)
        page_url = reverse("socialhub:profile_details", args=[profile.slug])
    first = shown[0]
    tab = tab if tab in shown else first
    context = {
        "object": profile, "profile": profile, "is_own_profile": own,
        "active_tab": tab, "page_url": page_url,
        "profile_tabs": own_account.profile_tabs(page_url, tab, shown),
        # The page's title names a tab other than the first.
        "active_tab_label": own_account.TAB_LABELS[tab][0] if tab != first else "",
    }
    if own:
        context["section_tabs"] = own_account.SECTION_TABS
        context.update(own_account.tab_context(request, tab, profile, **forms))
    if tab == "overview":
        context.update(_overview(request, profile, own))
    elif tab == "activity":
        context.update(_activity(request, profile, own))
    context = PageProcessor().decorate(context, request)
    if tab in PLUGIN_TABS and profile.pk:
        # Wallet is on the owner's strip alone, so it is drawn for nobody else.
        # A section that drew nothing (the mana plugin with no pools yet) is
        # left out, so a tab can say it has nothing.
        sections = [section for section in ProfilePlugin.render_tab(
                        tab, request=request, profile=profile, base_context=context)
                    if section.html.strip()]
        context["profile_plugin_sections"] = sections
        context["sections_before"] = [s for s in sections if s.order < REFERENCES_ORDER]
        context["sections_after"] = [s for s in sections if s.order >= REFERENCES_ORDER]
    response = render(request, "socialhub/profile_details.html", context, status=status)
    if own:
        # One's own sessions, addresses and contact details: kept by no
        # cache, the browser's back button included.
        add_never_cache_headers(response)
    return response


def render_own_profile(request, *, tab="", status=200, **forms):
    """``request.user``'s own page, drawn wherever the request is — at a
    door whose form came back with errors, or at ``/account/`` for an
    account with no profile yet. The profile shown is read again from the
    database: a form that failed has already written its values onto the
    member's own instance, and the page must not show them as saved."""
    person = getattr(request.user, "community_profile", None)
    shown = None
    if person is not None and person.pk:
        shown = profile_queryset(request.user).filter(pk=person.pk).first()
    if shown is None:
        # No profile yet (the console makes none): the edit form, and the
        # account's tabs, which need none. Not tied to the user, so nothing
        # unsaved is left on it.
        shown = Person(display_name=request.user.get_username())
    return render_profile(request, shown, own=True, tab=tab, status=status, **forms)


def _own_tab_return(request, section=""):
    """Where a door posted from one's own profile goes back to (2026-10-02,
    stage 50): the tab its form said it was on — one of ``OWN_TABS``, never
    anything else — at ``#section`` when the section is on that tab; or None
    when the form named no tab, and the door's Referer rule applies.

    The tab is posted rather than read off the Referer: behind the cloud's
    nginx the browser sends no path in it (``Referrer-Policy:
    strict-origin``), so these doors came back to the welcome page."""
    tab = request.POST.get(own_account.TAB_PARAM, "")
    if tab not in own_account.OWN_TABS:
        return None
    anchor = section if own_account.SECTION_TABS.get(section) == tab else ""
    return own_account.own_page_url(request.user, tab, anchor)


def _back(request, fallback, section=""):
    """The door's way back: the posted tab, else a Referer on this site,
    else ``fallback``."""
    return redirect(_own_tab_return(request, section)
                    or safe_next(request, request.META.get("HTTP_REFERER"), fallback))


_VALID_LANG_CODES = {code for code, _ in getattr(settings, "LANGUAGES", [])}


@login_required
def set_preferred_language(request):
    if request.method == "POST":
        lang = request.POST.get("language", "").strip()
        if lang in _VALID_LANG_CODES:
            try:
                profile = request.user.community_profile
                profile.preferred_language = lang
                profile.save(update_fields=["preferred_language"])
                translation.activate(lang)
                # Persist in session so it takes effect in the current session too.
                request.session["_language"] = lang
                messages.success(request, _("Language preference saved."))
            except Exception:
                messages.error(request, _("Could not save language preference."))
        else:
            messages.error(request, _("Invalid language selected."))

    # Sent from one's own profile, back to the tab it was on (stage 50).
    own = _own_tab_return(request, "language")
    if own:
        return redirect(own)
    # The Referer is whatever page sent the form, which need not be ours
    # (2026-09-30): followed only when it is a place on this site.
    referer = safe_next(request, request.META.get("HTTP_REFERER"))
    if referer:
        return redirect(referer)
    try:
        slug = request.user.community_profile.slug
        return redirect(reverse("socialhub:profile_details", args=[slug]))
    except Exception:
        return redirect("/")
