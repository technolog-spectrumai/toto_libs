from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from toto.socialhub.forms import CommunityNewsPostForm
from toto.socialhub.models import Community, CommunityNewsPost
from toto.socialhub.permissions import can_manage_community_news, current_person
from toto.ui import PageProcessor


def community_news_render(request, template_name, context):
    return render(request, template_name, PageProcessor().decorate(context, request))


def require_news_manager(request, community):
    if not can_manage_community_news(request, community):
        raise PermissionDenied("Only senior community members can manage community news.")


@login_required
def community_news_create(request, community_slug):
    community = get_object_or_404(Community, slug=community_slug)
    require_news_manager(request, community)

    initial = {"author": current_person(request)}

    if request.method == "POST":
        form = CommunityNewsPostForm(request.POST)
        if form.is_valid():
            post = form.save(commit=False)
            post.community = community
            post.save()
            form.save_m2m()
            messages.success(request, "Community news published.")
            return redirect(post.get_absolute_url())
    else:
        form = CommunityNewsPostForm(initial=initial)

    return community_news_render(request, "socialhub/community_news_form.html", {
        "form": form,
        "community": community,
        "title": "New community news",
        "submit_label": "Publish",
        "icon": "fa-solid fa-paper-plane",
    })


@login_required
def community_news_update(request, pk):
    post = get_object_or_404(CommunityNewsPost.objects.select_related("community"), pk=pk)
    require_news_manager(request, post.community)

    if request.method == "POST":
        form = CommunityNewsPostForm(request.POST, instance=post)
        if form.is_valid():
            post = form.save()
            messages.success(request, "Community news saved.")
            return redirect(post.get_absolute_url())
    else:
        form = CommunityNewsPostForm(instance=post)

    return community_news_render(request, "socialhub/community_news_form.html", {
        "form": form,
        "post": post,
        "community": post.community,
        "title": "Edit community news",
        "submit_label": "Save post",
        "icon": "fa-solid fa-pen-to-square",
    })


@login_required
def community_news_delete(request, pk):
    post = get_object_or_404(CommunityNewsPost.objects.select_related("community"), pk=pk)
    community = post.community
    require_news_manager(request, community)

    if request.method == "POST":
        post.delete()
        messages.success(request, "Community news deleted.")
        return redirect("socialhub:community_detail", slug=community.slug)

    return community_news_render(request, "socialhub/community_news_confirm_delete.html", {
        "post": post,
        "community": community,
    })
