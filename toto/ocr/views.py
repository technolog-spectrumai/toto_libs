from django.views.generic import ListView, DetailView
from django.shortcuts import get_object_or_404, redirect
from django.http import JsonResponse, HttpResponseBadRequest, Http404
from django.urls import reverse_lazy
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from toto.ui import PageProcessor
from .models import OcrProject, OcrImage, ImageTransform
from django.shortcuts import render
from django.urls import reverse
from django.contrib import messages
from .forms import ApplyTransformForm
from toto.bento.models import IdeaBox


# ---------------------------------------------------------
# ACCESS HELPERS
# ---------------------------------------------------------

def ensure_project_access(project, user):
    """Raise 404 if user cannot access the project."""
    if not project.user_has_access(user):
        raise Http404("Project not found")


def ensure_image_access(image, user):
    """Raise 404 if user cannot access the image's project."""
    if not image.project.user_has_access(user):
        raise Http404("Image not found")


def image_extracted_text(image):
    return "\n".join(
        line.text
        for line in image.lines.order_by("top", "left")
    ).strip()


# ---------------------------------------------------------
# PROJECT LIST
# ---------------------------------------------------------

class OcrProjectListView(LoginRequiredMixin, ListView):
    model = OcrProject
    template_name = "ocr/project_list.html"
    context_object_name = "projects"
    login_url = reverse_lazy("core:login")

    def get_queryset(self):
        user = self.request.user
        return (
            OcrProject.objects.filter(bucket__owner=user)
            | OcrProject.objects.filter(allowed_users=user)
        ).distinct().order_by("name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return PageProcessor().decorate(context, self.request)


# ---------------------------------------------------------
# PROJECT DETAIL
# ---------------------------------------------------------

class OcrProjectDetailView(LoginRequiredMixin, DetailView):
    model = OcrProject
    template_name = "ocr/project_detail.html"
    context_object_name = "project"
    slug_field = "slug"
    slug_url_kwarg = "slug"
    login_url = reverse_lazy("core:login")

    def get_object(self, queryset=None):
        project = get_object_or_404(OcrProject, slug=self.kwargs["slug"])
        ensure_project_access(project, self.request.user)
        return project

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.get_object()
        context["images"] = project.images.order_by("-uploaded_at")
        return PageProcessor().decorate(context, self.request)


# ---------------------------------------------------------
# IMAGE DETAIL
# ---------------------------------------------------------

class OcrImageDetailView(LoginRequiredMixin, DetailView):
    model = OcrImage
    template_name = "ocr/image_detail.html"
    context_object_name = "image"
    pk_url_kwarg = "image_id"
    login_url = reverse_lazy("core:login")

    def get_object(self, queryset=None):
        image = get_object_or_404(OcrImage, pk=self.kwargs["image_id"])
        ensure_image_access(image, self.request.user)
        return image

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        image = self.get_object()
        context["lines"] = image.lines.order_by("top", "left")
        context["extracted_text"] = image_extracted_text(image)
        context["transforms"] = ImageTransform.objects.prefetch_related("params").all()
        return PageProcessor().decorate(context, self.request)


# ---------------------------------------------------------
# RUN OCR
# ---------------------------------------------------------

@login_required
@csrf_exempt
def ocr_run(request, image_id):
    image = get_object_or_404(OcrImage, pk=image_id)
    ensure_image_access(image, request.user)

    lang = request.POST.get("lang", image.language)

    try:
        image.run_ocr(lang=lang)
        return JsonResponse({"status": "ok", "processed": True})
    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=500)


# ---------------------------------------------------------
# UPLOAD IMAGE
# ---------------------------------------------------------

@login_required
@csrf_exempt
def ocr_image_upload(request, slug):
    project = get_object_or_404(OcrProject, slug=slug)
    ensure_project_access(project, request.user)

    if request.method != "POST":
        return HttpResponseBadRequest("Invalid request method")

    if "image" not in request.FILES:
        return HttpResponseBadRequest("No image uploaded")

    uploaded_file = request.FILES["image"]

    OcrImage.objects.create(
        project=project,
        image=uploaded_file,
        filename=uploaded_file.name,
        file_size=uploaded_file.size,
        mime_type=uploaded_file.content_type or "",
    )

    return redirect("ocr:project_detail", slug=project.slug)


@login_required
@csrf_exempt
def ocr_image_delete(request, image_id):
    image = get_object_or_404(OcrImage, pk=image_id)
    ensure_image_access(image, request.user)

    if request.method != "POST":
        return HttpResponseBadRequest("Invalid request method")

    project_slug = image.project.slug
    image.delete()

    return redirect("ocr:project_detail", slug=project_slug)


@login_required
def apply_transform_view(request, image_id):
    image = get_object_or_404(OcrImage, pk=image_id)
    ensure_image_access(image, request.user)

    transform_id = request.GET.get("transform")
    transform = ImageTransform.objects.filter(pk=transform_id).first()

    if request.method == "POST":
        transform = ImageTransform.objects.get(pk=request.POST["transform"])
        form = ApplyTransformForm(request.POST, transform=transform)

        if form.is_valid():
            params = {
                key: value
                for key, value in form.cleaned_data.items()
                if key != "transform"
            }

            try:
                image.apply_transform(transform, params)
                messages.success(request, f"Transform '{transform.name}' applied successfully.")
            except Exception as e:
                messages.error(request, f"Error applying transform: {e}")

            return redirect(
                reverse("admin:ocr_ocrimage_change", args=[image.id])
            )

    else:
        form = ApplyTransformForm(transform=transform)

    return render(request, "admin/apply_transform.html", {
        "form": form,
        "image": image,
        "transform": transform,
    })


@login_required
def apply_transform(request, image_id):
    image = get_object_or_404(OcrImage, id=image_id)
    ensure_image_access(image, request.user)

    if request.method != "POST":
        return HttpResponseBadRequest("Invalid request method")

    transform_id = request.POST.get("transform_id")
    transform = get_object_or_404(ImageTransform, id=transform_id)

    try:
        user_params = {}
        for p in transform.params.all():
            key = f"param_{p.key}"
            if key in request.POST:
                user_params[p.key] = int(request.POST[key])

        image.apply_transform(transform, user_params)
        messages.success(request, f"Transform '{transform.name}' applied successfully.")
    except Exception as exc:
        messages.error(request, f"Transform '{transform.name}' failed: {exc}")

    return redirect("ocr:image_detail", image_id=image.id)


@login_required
def image_duplicate(request, image_id):
    image = get_object_or_404(OcrImage, id=image_id)
    ensure_image_access(image, request.user)

    if request.method == "POST":
        new_name = request.POST.get("new_name") or f"{image.filename} (copy)"
        new_image = image.clone(copy_lines=True)
        new_image.filename = new_name
        new_image.save(update_fields=["filename"])
        return redirect("ocr:image_detail", image_id=new_image.id)

    return redirect("ocr:image_detail", image_id=image.id)


@login_required
def promote_image_text_to_bento(request, image_id):
    image = get_object_or_404(OcrImage, pk=image_id)
    ensure_image_access(image, request.user)

    if request.method != "POST":
        return HttpResponseBadRequest("Invalid request method")

    text = image_extracted_text(image)
    if not text:
        messages.error(request, "There is no extracted text to promote yet.")
        return redirect("ocr:image_detail", image_id=image.id)

    title = request.POST.get("title") or f"OCR note: {image.filename or image.pk}"
    source_url = request.build_absolute_uri(
        reverse("ocr:image_detail", kwargs={"image_id": image.id})
    )

    box = IdeaBox.objects.create(
        title=title[:160],
        body=text,
        is_concept=False,
        source_title=image.filename or str(image),
        source_url=source_url[:200],
        source_type="ocr-image",
        properties={
            "ocr_image_id": image.id,
            "ocr_project_id": image.project_id,
            "ocr_project": image.project.name,
            "language": image.language,
            "processed": image.processed,
            "processed_at": image.processed_at.isoformat() if image.processed_at else None,
        },
    )

    messages.success(request, "OCR text promoted to Bento.")
    return redirect("bento:box_detail", pk=box.pk)
