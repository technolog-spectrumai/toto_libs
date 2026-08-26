from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext_lazy as _
from django.views.generic import ListView, DetailView

from toto.people.models import Person
from toto.socialhub.models import Community
from toto.socialhub.plugins.profile_plugins import ProfilePlugin
from toto.ui import PageProcessor


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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return PageProcessor().decorate(context, self.request)


class ProfileDetailView(LoginRequiredMixin, DetailView):
    model = Person
    template_name = "socialhub/profile_details.html"
    context_object_name = "profile"

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .prefetch_related("communities")
            .select_related(
                "address",
                "user",
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # `self.object`, NOT get_object(): DetailView has already fetched it,
        # and re-fetching runs the queryset again.
        profile = self.object

        is_own_profile = profile.user == self.request.user

        if is_own_profile:
            context["reference_requests"] = profile.sent_references.select_related(
                "application",
                "application__community",
            ).order_by("-created_at")
        else:
            context["reference_requests"] = None

        context["is_own_profile"] = is_own_profile

        # The SAME resolver the People map uses, so the page and the map cannot
        # disagree about who may see an address — the failure `vault/access.py`
        # calls "the listing and the door drift apart".
        #
        # `address_shown` is what the resolver decided may be published, not the
        # raw field: an approximate sharer's locality rather than their street.
        from toto.locations.people_access import may_see_location, place_label

        may_see = may_see_location(self.request.user, profile)
        context["may_see_address"] = may_see
        if is_own_profile:
            # Your own address in full, whatever you share with others.
            # `place_label` deliberately coarsens for an approximate sharer, and
            # applying that to the owner would show somebody their own street
            # as a locality and read as data loss.
            context["address_shown"] = str(profile.address) if profile.address_id else ""
        else:
            context["address_shown"] = place_label(profile) if may_see else ""

        context = PageProcessor().decorate(context, self.request)

        context["profile_plugin_sections"] = ProfilePlugin.render_all(
            request=self.request,
            profile=profile,
            base_context=context,
        )

        return context


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

    referer = request.META.get("HTTP_REFERER")
    if referer:
        return redirect(referer)
    try:
        slug = request.user.community_profile.slug
        return redirect(reverse("socialhub:profile_details", args=[slug]))
    except Exception:
        return redirect("/")


@login_required
def set_location_sharing(request):
    """Switch appearing on the People map on or off, and at what precision.

    Deliberately its own door rather than a field on a bigger profile form: this
    is the one setting whose wrong value publishes where somebody lives, and a
    setting like that should not be able to change as a side effect of saving
    something else.

    Off is always accepted, even from a person with no address at all — turning
    it off must never be the thing that fails.
    """
    from toto.people.models import LocationSharing

    if request.method != "POST":
        return redirect("socialhub:profile_list")

    choice = (request.POST.get("location_sharing") or "").strip()
    if choice not in LocationSharing.values:
        messages.error(request, _("That is not a sharing setting."))
        return redirect(request.META.get("HTTP_REFERER", reverse("socialhub:profile_list")))

    profile = getattr(request.user, "community_profile", None)
    if profile is None:
        messages.error(request, _("You have no profile to share."))
        return redirect("socialhub:profile_list")

    profile.location_sharing = choice
    profile.save(update_fields=["location_sharing"])

    if choice == LocationSharing.OFF:
        messages.success(request, _("You no longer appear on the People map."))
    elif not profile.address_id:
        # Honest rather than silently useless: the setting IS saved, and it will
        # start working the moment an address exists.
        messages.warning(request, _(
            "Saved — but you have no address on your profile yet, so nobody can "
            "see you on the map until you add one."))
    elif choice == LocationSharing.APPROXIMATE:
        messages.success(request, _(
            "You now appear on the People map, as an approximate area rather "
            "than an exact address."))
    else:
        messages.success(request, _(
            "You now appear on the People map at your exact address."))
    return redirect(request.META.get("HTTP_REFERER", reverse("socialhub:profile_list")))
