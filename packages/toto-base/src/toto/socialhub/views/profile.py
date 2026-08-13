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
from django.views.generic import ListView, DetailView

from toto.people.models import Person
from toto.quota import rates
from toto.socialhub.models import Community, Station
from toto.socialhub.plugins.profile_plugins import ProfilePlugin
from toto.ui import PageProcessor


class ProfileListView(ListView):
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
        prefetches = [
            "communities",
            # Offices, filtered and ordered here so the template neither
            # filters nor sorts, and so a vacated one never appears.
            Prefetch(
                "stations",
                queryset=Station.objects.filter(active=True)
                .select_related("serves")
                .order_by("serves__name", "name"),
                to_attr="held_stations",
            ),
        ]
        return (
            super()
            .get_queryset()
            .prefetch_related(*prefetches)
            .select_related(
                "address",
                "user",
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # `self.object`, NOT get_object(): DetailView has already fetched it,
        # and re-fetching runs the queryset again and throws the prefetch above
        # away — the offices would then cost one query each.
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

        # Offices. The roster's public facts render for anyone; the PAY renders
        # only to its holder. The profile page is visible to every logged-in
        # user for any person, so an unconditional stipend would publish every
        # officer's pay platform-wide — see STATIONS.md, "Visibility".
        context["stations"] = getattr(profile, "held_stations", [])
        show_pay = is_own_profile and rates.pricing_enabled()
        context["show_station_pay"] = show_pay
        # Absent rather than blank on a host that does not bill: a currency
        # symbol with no currency behind it is a worse answer than no line.
        context["station_pay_asset"] = rates.price_asset_symbol() if show_pay else ""

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

