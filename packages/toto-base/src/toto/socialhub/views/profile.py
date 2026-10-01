from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Prefetch
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django.views.generic import ListView, DetailView

from toto.core.safe_next import safe_next
from toto.people.models import Person
from toto.socialhub.models import Community
from toto.socialhub.plugins.profile_plugins import ProfilePlugin
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
        return PageProcessor().decorate(context, self.request)


class ProfileDetailView(LoginRequiredMixin, DetailView):
    model = Person
    template_name = "socialhub/profile_details.html"
    context_object_name = "profile"

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .prefetch_related(listed_communities(self.request.user))
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
        from toto.locations.geocode import (geocoding_enabled,
                                            geocoding_settings)
        context["geocoding_enabled"] = geocoding_enabled(geocoding_settings())

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
        return redirect(safe_next(request, request.META.get("HTTP_REFERER"),
                                    reverse("socialhub:profile_list")))

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
    return redirect(safe_next(request, request.META.get("HTTP_REFERER"),
                                    reverse("socialhub:profile_list")))

@login_required
def set_my_address(request):
    """Place (or move) your own pin — the address the People map shares.

    The other half of `set_location_sharing`: that door decides WHO may see
    the address, this one is the only door that can WRITE it. Own profile
    only, by construction — there is no way to name anybody else.

    The Address row is updated in place rather than replaced, so nothing
    referencing it dangles and a re-save moves the pin instead of minting
    rows. The pin alone is exactly as useful on the map. Its street and town
    come from a reverse lookup only when asked for (2026-09-28): that is a
    charged place lookup now (`toto.locations.geocoding`), so it has its own
    priced button, "Save and look up the address", and a plain Save asks
    nobody. A refused lookup never loses the pin: it is saved, and the member
    is told why its address was not.
    """
    from toto.locations import geocoding
    from toto.locations.models import Address

    if request.method != "POST":
        return redirect("socialhub:profile_list")

    try:
        latitude = float(request.POST.get("latitude", ""))
        longitude = float(request.POST.get("longitude", ""))
    except (TypeError, ValueError):
        messages.error(request, _("Place the pin on the map first."))
        return redirect(safe_next(request, request.META.get("HTTP_REFERER"),
                                           reverse("socialhub:profile_list")))
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        messages.error(request, _("Place the pin on the map first."))
        return redirect(safe_next(request, request.META.get("HTTP_REFERER"),
                                           reverse("socialhub:profile_list")))

    profile = getattr(request.user, "community_profile", None)
    if profile is None:
        # First contact with the map creates the profile row, the same way
        # the truth-book host's my-location page did.
        profile = Person.objects.create(
            user=request.user,
            display_name=request.user.get_username())

    fields = {"latitude": latitude, "longitude": longitude}
    refused = None
    answer = None
    if request.POST.get("lookup_address"):
        try:
            answer = geocoding.reverse(request.user, latitude, longitude)
        except geocoding.REFUSALS as exc:
            refused = exc
        else:
            # The answer describes THIS pin, blanks included: a moved pin
            # must not keep the old street under a new town.
            for key, value in answer["fields"].items():
                limit = Address._meta.get_field(key).max_length
                fields[key] = str(value or "")[:limit]

    if profile.address_id:
        # On a GIS build the geometry is authoritative and save() overwrites
        # the floats from it — so a moved pin must drop the old geometry, and
        # save() then derives a fresh one from the new coordinates. Without
        # this the pin silently refuses to move on exactly the hosts with GIS.
        if hasattr(profile.address, "geometry"):
            profile.address.geometry = None
        for key, value in fields.items():
            setattr(profile.address, key, value)
        profile.address.save()
    else:
        profile.address = Address.objects.create(**fields)
        profile.save(update_fields=["address"])

    if refused is not None:
        messages.warning(request, _(
            "Your pin is saved, but its address was not looked up: %(reason)s")
            % {"reason": refused})
    elif answer is not None and not answer["found"]:
        messages.success(request, _(
            "Your pin is saved. No street address is known at that point."))
    else:
        messages.success(request, _("Your address is saved."))
    return redirect(safe_next(request, request.META.get("HTTP_REFERER"),
                              reverse("socialhub:profile_details", args=[profile.slug])))


@require_POST
@login_required
def search_address(request):
    """Forward-geocode a typed place name, for the picker's search box.

    Proxied through the server rather than fetched from the browser so the
    host's `LOCATIONS_GEOCODING` config is the single gate: a host that makes
    no outbound calls answers 404 here and renders no search box. Each search
    is a charged place lookup (`toto.locations.geocoding`, 2026-09-28), so it
    is a POST with the CSRF token, sent on Enter or the Search button and
    never per keystroke, and a refusal answers ``{"error"}`` with its own
    status: 402 out of mana, 429 too many, 503 provider down, 404 off here.

    Its own door rather than `locations:geocode_search`: socialhub is free on
    every plan, the locations app is not, and setting your own address must
    not need a plan.
    """
    from django.http import JsonResponse

    from toto.locations import geocoding

    try:
        results = geocoding.search(request.user, request.POST.get("q", ""))
    except geocoding.REFUSALS as exc:
        response = JsonResponse({"error": str(exc)}, status=exc.status_code)
        if getattr(exc, "retry_after", None):
            response["Retry-After"] = str(exc.retry_after)
        return response
    return JsonResponse({"results": results})
