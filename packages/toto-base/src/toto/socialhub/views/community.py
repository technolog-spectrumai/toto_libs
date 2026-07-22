from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.generic import ListView, DetailView
from toto.people.models import Person
from toto.socialhub.models import Community
from toto.socialhub.permissions import current_person
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
        person = current_person(self.request)
        context["viewer_is_federal_agent"] = bool(person and person.is_federal_agent)
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


def community_chain_graph_data(request, slug):
    person = current_person(request)
    if not (person and person.is_federal_agent):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    all_communities = Community.objects.select_related("parent", "head").all()

    nodes = []
    edges = []
    seen_people = {}

    for c in all_communities:
        nodes.append({
            "id": f"c-{c.pk}",
            "label": c.name,
            "type": "community",
            "shape": "roundrectangle",
            "url": reverse("socialhub:community_detail", kwargs={"slug": c.slug}),
        })
        if c.parent_id:
            edges.append({
                "source": f"c-{c.parent_id}",
                "target": f"c-{c.pk}",
                "label": "",
                "type": "parent_child",
            })
        if c.head_id and c.head_id not in seen_people:
            seen_people[c.head_id] = c.head
            nodes.append({
                "id": f"p-{c.head_id}",
                "label": c.head.display_name,
                "type": "person",
                "shape": "ellipse",
                "url": reverse("socialhub:profile_details", kwargs={"slug": c.head.slug}),
            })
        if c.head_id:
            edges.append({
                "source": f"c-{c.pk}",
                "target": f"p-{c.head_id}",
                "label": "head",
                "type": "head",
            })

    return JsonResponse({"nodes": nodes, "edges": edges})


class AdministrataView(DetailView):
    model = Community
    template_name = "socialhub/administrata.html"
    context_object_name = "community"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def dispatch(self, request, *args, **kwargs):
        person = current_person(request)
        if not (person and person.is_federal_agent):
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden()
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context = PageProcessor().decorate(context, self.request)
        context["graph_data_url"] = reverse(
            "socialhub:community_chain_graph_data",
            kwargs={"slug": self.object.slug},
        )
        return context
