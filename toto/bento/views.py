from cryptography.exceptions import InvalidTag
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from toto.ui import PageProcessor

from .forms import CategoryForm, IdeaBoxForm, IdeaLinkForm
from .models import Category, IdeaBox, IdeaLink


def bento_render(request, template_name, context):
    return render(request, template_name, PageProcessor().decorate(context, request))


def filtered_boxes(request):
    query = request.GET.get("q", "")
    concept_filter = request.GET.get("concept", "")

    boxes = IdeaBox.objects.select_related("category")

    if query:
        boxes = boxes.filter(
            Q(label__icontains=query)
            | Q(properties__body__icontains=query)
            | Q(properties__source_title__icontains=query)
            | Q(properties__source_type__icontains=query)
            | Q(properties__quote__icontains=query)
        )

    if concept_filter == "yes":
        boxes = boxes.filter(properties__is_concept=True)
    elif concept_filter == "no":
        boxes = boxes.exclude(properties__is_concept=True)

    return boxes, query, concept_filter


def serialize_box(box):
    properties = dict(box.properties)
    if box.is_locked:
        properties["body"] = None
    return {
        "id": box.pk,
        "label": box.label or _("Untitled box"),
        "is_locked": box.is_locked,
        "is_concept": box.is_concept,
        "category": {
            "id": box.category_id,
            "name": str(box.category),
            "slug": box.category.slug,
        } if box.category_id else None,
        "properties": properties,
        "created_at": box.created_at.isoformat(),
        "updated_at": box.updated_at.isoformat(),
    }


def serialize_link(link):
    return {
        "id": link.pk,
        "from_box": link.from_box_id,
        "to_box": link.to_box_id,
        "label": link.label or _("related to"),
        "properties": link.properties,
        "created_at": link.created_at.isoformat(),
    }


def box_list(request):
    boxes, query, concept_filter = filtered_boxes(request)

    return bento_render(request, "bento/box_list.html", {
        "boxes": boxes,
        "query": query,
        "concept_filter": concept_filter,
        "total_boxes": IdeaBox.objects.count(),
        "concept_count": IdeaBox.objects.filter(properties__is_concept=True).count(),
        "note_count": IdeaBox.objects.exclude(properties__is_concept=True).count(),
        "link_count": IdeaLink.objects.count(),
    })


def box_detail(request, pk):
    box = get_object_or_404(IdeaBox.objects.select_related("category"), pk=pk)
    outgoing_links = box.outgoing_links.select_related("to_box")
    incoming_links = box.incoming_links.select_related("from_box")

    return bento_render(request, "bento/box_detail.html", {
        "box": box,
        "outgoing_links": outgoing_links,
        "incoming_links": incoming_links,
        "can_lock": _is_federal_agent(request),
    })


def box_create(request):
    if request.method == "POST":
        form = IdeaBoxForm(request.POST)
        if form.is_valid():
            box = form.save()
            return redirect("bento:box_detail", pk=box.pk)
    else:
        form = IdeaBoxForm()

    return bento_render(request, "bento/box_form.html", {"form": form, "title": _("New box")})


def box_update(request, pk):
    box = get_object_or_404(IdeaBox, pk=pk)

    if request.method == "POST":
        form = IdeaBoxForm(request.POST, instance=box)
        if form.is_valid():
            box = form.save()
            return redirect("bento:box_detail", pk=box.pk)
    else:
        form = IdeaBoxForm(instance=box)

    return bento_render(request, "bento/box_form.html", {"form": form, "title": _("Edit box"), "box": box})


def box_delete(request, pk):
    box = get_object_or_404(IdeaBox, pk=pk)

    if request.method == "POST":
        box.delete()
        return redirect("bento:box_list")

    return bento_render(request, "bento/box_confirm_delete.html", {"box": box})


def link_create(request):
    initial = {}
    from_box_id = request.GET.get("from")
    to_box_id = request.GET.get("to")

    if from_box_id:
        initial["from_box"] = from_box_id
    if to_box_id:
        initial["to_box"] = to_box_id

    if request.method == "POST":
        form = IdeaLinkForm(request.POST)
        if form.is_valid():
            link = form.save()
            return redirect("bento:box_detail", pk=link.from_box.pk)
    else:
        form = IdeaLinkForm(initial=initial)

    return bento_render(request, "bento/link_form.html", {"form": form, "title": _("New link")})


def link_delete(request, pk):
    link = get_object_or_404(IdeaLink, pk=pk)
    from_box_pk = link.from_box.pk

    if request.method == "POST":
        link.delete()
        return redirect("bento:box_detail", pk=from_box_pk)

    return bento_render(request, "bento/link_confirm_delete.html", {"link": link})


def category_list(request):
    categories = Category.objects.prefetch_related("idea_boxes")

    return bento_render(request, "bento/category_list.html", {
        "categories": categories,
    })


def category_create(request):
    if request.method == "POST":
        form = CategoryForm(request.POST)
        if form.is_valid():
            category = form.save()
            return redirect("bento:category_update", pk=category.pk)
    else:
        form = CategoryForm()

    return bento_render(request, "bento/category_form.html", {"form": form, "title": _("New category")})


def category_update(request, pk):
    category = get_object_or_404(Category, pk=pk)

    if request.method == "POST":
        form = CategoryForm(request.POST, instance=category)
        if form.is_valid():
            form.save()
            return redirect("bento:category_list")
    else:
        form = CategoryForm(instance=category)

    return bento_render(request, "bento/category_form.html", {"form": form, "title": _("Edit category"), "category": category})


def api_boxes(request):
    boxes, query, concept_filter = filtered_boxes(request)
    return JsonResponse({
        "query": query,
        "concept": concept_filter,
        "count": boxes.count(),
        "results": [serialize_box(box) for box in boxes[:100]],
    })


def _is_federal_agent(request) -> bool:
    person = getattr(request.user, "community_profile", None)
    return bool(person and getattr(person, "is_federal_agent", False))


@require_POST
def box_lock(request, pk):
    if not _is_federal_agent(request):
        return JsonResponse({"ok": False, "error": str(_("Only federal agents can lock boxes."))}, status=403)
    box = get_object_or_404(IdeaBox, pk=pk)
    password = request.POST.get("password", "").strip()
    if not password:
        return JsonResponse({"ok": False, "error": str(_("Password is required."))}, status=400)
    if box.is_locked:
        return JsonResponse({"ok": False, "error": str(_("Box is already locked."))}, status=400)
    try:
        box.lock(password)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)
    return JsonResponse({"ok": True})


@require_POST
def box_unlock(request, pk):
    if not _is_federal_agent(request):
        return JsonResponse({"ok": False, "error": str(_("Only federal agents can unlock boxes."))}, status=403)
    box = get_object_or_404(IdeaBox, pk=pk)
    password = request.POST.get("password", "").strip()
    if not password:
        return JsonResponse({"ok": False, "error": str(_("Password is required."))}, status=400)
    if not box.is_locked:
        return JsonResponse({"ok": False, "error": str(_("Box is not locked."))}, status=400)
    try:
        box.unlock(password)
    except InvalidTag:
        return JsonResponse({"ok": False, "error": str(_("Wrong password."))}, status=400)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)
    return JsonResponse({"ok": True})


def api_box_graph(request, pk):
    box = get_object_or_404(IdeaBox, pk=pk)
    outgoing_links = list(box.outgoing_links.select_related("to_box"))
    incoming_links = list(box.incoming_links.select_related("from_box"))
    related_boxes = {box.pk: box}

    for link in outgoing_links:
        related_boxes[link.to_box.pk] = link.to_box
    for link in incoming_links:
        related_boxes[link.from_box.pk] = link.from_box

    return JsonResponse({
        "focus": box.pk,
        "nodes": [serialize_box(related_box) for related_box in related_boxes.values()],
        "links": [serialize_link(link) for link in outgoing_links + incoming_links],
    })