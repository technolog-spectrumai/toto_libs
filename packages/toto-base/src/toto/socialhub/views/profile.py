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
        prefetches = ["communities"]
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
        profile = self.get_object()

        is_own_profile = profile.user == self.request.user

        if is_own_profile:
            context["reference_requests"] = profile.sent_references.select_related(
                "application",
                "application__community",
            ).order_by("-created_at")
        else:
            context["reference_requests"] = None

        context["is_own_profile"] = is_own_profile

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

