from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.db import models as db_models

from toto.api.cors import CorsApiView, MeshGatedApiView
from toto.people.models import Person
from toto.socialhub.models import Community


def _profile_to_dict(request, person):
    avatar_url = None
    try:
        if person.avatar:
            avatar_url = request.build_absolute_uri(person.avatar.url)
    except Exception:
        pass
    return {
        "id": person.id,
        "slug": person.slug,
        "full_name": person.full_name,
        "display_name": person.display_name,
        "avatar_url": avatar_url,
        "community_count": person.communities.count(),
    }


def _community_to_dict(c):
    return {
        "id": c.id,
        "slug": c.slug,
        "name": c.name,
        "org_type": c.org_type,
        "org_type_label": c.get_org_type_display(),
        "established_year": c.established_year,
        "member_count": c.senior_members.count(),
        "head_name": c.head.full_name if c.head else None,
    }


@method_decorator(csrf_exempt, name="dispatch")
class ProfileListApiView(MeshGatedApiView):
    def get(self, request):
        profiles = Person.objects.prefetch_related("communities").order_by("display_name")[:100]
        return JsonResponse({"profiles": [_profile_to_dict(request, p) for p in profiles]})


@method_decorator(csrf_exempt, name="dispatch")
class ProfileDetailApiView(MeshGatedApiView):
    def get(self, request, slug):
        try:
            person = Person.objects.prefetch_related("communities").get(slug=slug)
        except Person.DoesNotExist:
            return JsonResponse({"error": "Profile not found."}, status=404)
        data = _profile_to_dict(request, person)
        data["communities"] = [
            {"id": c.id, "name": c.name, "slug": c.slug, "org_type": c.org_type}
            for c in person.communities.all()
        ]
        return JsonResponse(data)


@method_decorator(csrf_exempt, name="dispatch")
class CommunityListApiView(MeshGatedApiView):
    def get(self, request):
        communities = Community.objects.prefetch_related("senior_members").order_by("name")[:100]
        return JsonResponse({"communities": [_community_to_dict(c) for c in communities]})


@method_decorator(csrf_exempt, name="dispatch")
class CommunityDetailApiView(MeshGatedApiView):
    def get(self, request, slug):
        try:
            community = Community.objects.prefetch_related("senior_members").get(slug=slug)
        except Community.DoesNotExist:
            return JsonResponse({"error": "Community not found."}, status=404)
        data = _community_to_dict(community)
        data["members"] = [
            {"id": p.id, "slug": p.slug, "name": p.full_name}
            for p in community.senior_members.all()[:50]
        ]
        try:
            latest_news = community.news_posts.order_by("-created_at").first()
            data["latest_news_title"] = latest_news.title if latest_news else None
        except Exception:
            data["latest_news_title"] = None
        return JsonResponse(data)


@method_decorator(csrf_exempt, name="dispatch")
class CommunityOrgChartApiView(MeshGatedApiView):
    def get(self, request, slug):
        try:
            community = Community.objects.get(slug=slug)
        except Community.DoesNotExist:
            return JsonResponse({"error": "Community not found."}, status=404)
        members = Person.objects.filter(communities=community).select_related("patron")
        nodes = []
        for m in members:
            avatar_url = None
            try:
                if m.avatar:
                    avatar_url = request.build_absolute_uri(m.avatar.url)
            except Exception:
                pass
            nodes.append({
                "id": str(m.id),
                "name": m.display_name,
                "slug": m.slug,
                "pid": str(m.patron_id) if m.patron_id else None,
                "avatar_url": avatar_url,
                "email": m.email or "",
                "phone": m.phone or "",
            })
        return JsonResponse({"nodes": nodes})
