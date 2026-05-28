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

        is_own_profile = profile.user == self.request.user

        if is_own_profile:
            context["reference_requests"] = profile.sent_references.select_related(
                "application",
                "application__community",
            ).order_by("-created_at")
        else:
            context["reference_requests"] = None

        # For own profile: find communities with an active constitution the person hasn't signed.
        unsigned_constitutions = {}
        if is_own_profile:
            from toto.socialhub.models import Constitution, ConstitutionSignature
            community_ids = list(profile.communities.values_list("pk", flat=True))
            active_constitutions = {
                c.community_id: c
                for c in Constitution.objects.filter(
                    community_id__in=community_ids, is_active=True
                )
            }
            already_signed = set(
                ConstitutionSignature.objects.filter(
                    constitution__in=active_constitutions.values(),
                    person=profile,
                    signed_at__isnull=False,
                ).values_list("constitution__community_id", flat=True)
            )
            unsigned_constitutions = {
                cid: c
                for cid, c in active_constitutions.items()
                if cid not in already_signed
            }
        context["unsigned_constitutions"] = unsigned_constitutions

        context = PageProcessor().decorate(context, self.request)

        context["profile_plugin_sections"] = ProfilePlugin.render_all(
            request=self.request,
            profile=profile,
            base_context=context,
        )

        return context





