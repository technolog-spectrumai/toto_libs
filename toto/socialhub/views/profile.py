from django.views.generic import ListView, DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
from toto.people.models import Person
from toto.socialhub.models import Community
from toto.socialhub.plugins.profile_plugins import ProfilePlugin
from toto.ui import PageProcessor
from django.shortcuts import redirect
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


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
        from django.apps import apps
        prefetches = ["communities"]
        if apps.is_installed("toto.competence"):
            prefetches.append("experiences")
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

        if profile.user == self.request.user:
            context["reference_requests"] = profile.sent_references.select_related(
                "application",
                "application__community",
            ).order_by("-created_at")
        else:
            context["reference_requests"] = None

        context = PageProcessor().decorate(context, self.request)

        context["profile_plugin_sections"] = ProfilePlugin.render_all(
            request=self.request,
            profile=profile,
            base_context=context,
        )

        return context





