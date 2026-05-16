from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.generic import ListView, DetailView
from toto.people.models import Person
from toto.socialhub.models import Community
from toto.socialhub.plugins.community_plugins import CommunityPlugin
from toto.ui import PageProcessor
from django.http import JsonResponse



class CommunityListView(ListView):
    model = Community
    template_name = "socialhub/community_list.html"
    context_object_name = "communities"
    paginate_by = 10

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return PageProcessor().decorate(context, self.request)


class CommunityDetailView(DetailView):
    model = Community
    template_name = "socialhub/community_details.html"
    context_object_name = "community"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        community = self.object
        context["fetch_url"] = reverse(
            "socialhub:community_org_chart_data_by_slug",
            kwargs={"company_slug": community.slug}
        )
        context = PageProcessor().decorate(context, self.request)
        context["community_plugin_sections"] = CommunityPlugin.render_all(
            request=self.request,
            community=community,
            base_context=context,
        )
        return context


def community_org_chart_data_by_slug(request, company_slug):
    community = get_object_or_404(Community, slug=company_slug)

    # Members belonging to this community
    members = Person.objects.filter(
        communities=community
    ).select_related("user", "patron")

    nodes = []

    for m in members:
        nodes.append({
            "id": str(m.id),
            "name": m.display_name,
            "title": "",  # you have no title field
            "pid": str(m.patron_id) if m.patron_id else None,  # hierarchy via patron
            "profile_url": f"/socialhub/profiles/{m.slug}/",
        })

    return JsonResponse({"nodes": nodes})
