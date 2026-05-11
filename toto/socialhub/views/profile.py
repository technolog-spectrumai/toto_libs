from django.views.generic import ListView, DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
from toto.socialhub.models import Person, Community, Experience
from toto.core.page import PageProcessor
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
        return (
            super()
            .get_queryset()
            .prefetch_related(
                "communities",
                "experiences",
            )
            .select_related(
                "address",
                "user",
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = self.get_object()

        context["experiences"] = profile.experiences.all()

        # Only show reference requests if viewing your own profile
        if profile.user == self.request.user:
            context["reference_requests"] = profile.sent_references.select_related(
                "application",
                "application__community",
            ).order_by("-created_at")
        else:
            context["reference_requests"] = None

        return PageProcessor().decorate(context, self.request)


@login_required
def my_profile_redirect(request):
    profile = getattr(request.user, "community_profile", None)
    if profile is None:
        return redirect("core:dashboard")
    return redirect("socialhub:profile_details", slug=profile.slug)



