"""One's own home on the map: the pin, the place search, who may see it
(2026-10-04).

These three doors were the socialhub's (``set_my_address``,
``search_address``, ``set_location_sharing``) while a person's address was a
key to a map address on ``people.Person``. toto-base carries no geography now
— the profile's address is text — so the pin and its sharing switch are this
app's ``Home`` row, and the doors are here. The form is the profile plugin's
(``plugins/profile_plugins.py``).

Own profile only, by construction: no door names anybody else.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from toto.people.models import Person

from .models import Address, Home, HomeSharing

SECTION = "#home-on-the-map"


def _own_page(profile):
    if profile is None or not profile.slug:
        return reverse("socialhub:profile_list")
    return reverse("socialhub:profile_details", args=[profile.slug]) + SECTION


@login_required
def set_home_sharing(request):
    """Switch appearing on the People map on or off, and at what precision.

    Its own door: this is the one setting whose wrong value publishes where
    somebody lives. Off is always accepted, even from a person with no pin at
    all — turning it off must never be the thing that fails.
    """
    if request.method != "POST":
        return redirect("socialhub:profile_list")

    profile = getattr(request.user, "community_profile", None)
    choice = (request.POST.get("sharing") or "").strip()
    if choice not in HomeSharing.values:
        messages.error(request, _("That is not a sharing setting."))
        return redirect(_own_page(profile))
    if profile is None:
        messages.error(request, _("You have no profile to share."))
        return redirect("socialhub:profile_list")

    home, _created = Home.objects.get_or_create(person=profile)
    home.sharing = choice
    home.save(update_fields=["sharing"])

    if choice == HomeSharing.OFF:
        messages.success(request, _("You no longer appear on the People map."))
    elif not home.address_id:
        messages.warning(request, _(
            "Saved — but you have no address on your profile yet, so nobody can "
            "see you on the map until you add one."))
    elif choice == HomeSharing.APPROXIMATE:
        messages.success(request, _(
            "You now appear on the People map, as an approximate area rather "
            "than an exact address."))
    else:
        messages.success(request, _(
            "You now appear on the People map at your exact address."))
    return redirect(_own_page(profile))


@login_required
def set_my_home(request):
    """Place (or move) your own pin — the address the People map shares.

    The Address row is updated in place rather than replaced, so a re-save
    moves the pin instead of minting rows. Its street and town come from a
    reverse lookup only when asked for: that is a charged place lookup
    (``geocoding``), so it has its own priced button, and a plain Save asks
    nobody. A refused lookup never loses the pin.
    """
    from . import geocoding

    if request.method != "POST":
        return redirect("socialhub:profile_list")

    profile = getattr(request.user, "community_profile", None)
    try:
        latitude = float(request.POST.get("latitude", ""))
        longitude = float(request.POST.get("longitude", ""))
    except (TypeError, ValueError):
        messages.error(request, _("Place the pin on the map first."))
        return redirect(_own_page(profile))
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        messages.error(request, _("Place the pin on the map first."))
        return redirect(_own_page(profile))

    if profile is None:
        profile = Person.objects.create(
            user=request.user, display_name=request.user.get_username())

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

    home, _created = Home.objects.get_or_create(person=profile)
    if home.address_id:
        # On a GIS build the geometry is authoritative and save() overwrites
        # the floats from it — so a moved pin must drop the old geometry.
        pin = home.address
        if hasattr(pin, "geometry"):
            pin.geometry = None
        for key, value in fields.items():
            setattr(pin, key, value)
        pin.save()
    else:
        home.address = Address.objects.create(**fields)
        home.save(update_fields=["address"])

    if refused is not None:
        messages.warning(request, _(
            "Your pin is saved, but its address was not looked up: %(reason)s")
            % {"reason": refused})
    elif answer is not None and not answer["found"]:
        messages.success(request, _(
            "Your pin is saved. No street address is known at that point."))
    else:
        messages.success(request, _("Your address is saved."))
    return redirect(_own_page(profile))


@require_POST
@login_required
def search_home(request):
    """Forward-geocode a typed place name, for the picker's search box: a
    charged place lookup, so a POST with the CSRF token, and a refusal answers
    ``{"error"}`` with its own status (402, 429, 503, 404)."""
    from . import geocoding

    try:
        results = geocoding.search(request.user, request.POST.get("q", ""))
    except geocoding.REFUSALS as exc:
        response = JsonResponse({"error": str(exc)}, status=exc.status_code)
        if getattr(exc, "retry_after", None):
            response["Retry-After"] = str(exc.retry_after)
        return response
    return JsonResponse({"results": results})
