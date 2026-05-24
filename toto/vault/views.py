import mimetypes

from django.http import FileResponse, JsonResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404
from django.views import View
from django.views.generic import ListView, DetailView
from django.urls import reverse
from django.contrib.auth.mixins import LoginRequiredMixin

from toto.ui import PageProcessor
from .models import VaultFile, Bucket, FileGateway


# ============================================================
# Public File Views
# ============================================================

class PublicFileListView(ListView):
    """
    Shows all public files; optionally filter by bucket.
    """
    model = VaultFile
    template_name = "vault/public_file_list.html"
    context_object_name = "files"
    paginate_by = 10

    def get_queryset(self):
        queryset = VaultFile.objects.filter(is_public=True).select_related("owner", "bucket").order_by("-uploaded_at")
        bucket_slug = self.request.GET.get("bucket")
        if bucket_slug:
            queryset = queryset.filter(bucket__slug=bucket_slug)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        bucket_slug = self.request.GET.get("bucket", "")
        context["buckets"] = Bucket.objects.all()
        context["selected_bucket"] = bucket_slug

        gateway_url = None
        if bucket_slug:
            bucket = Bucket.objects.filter(slug=bucket_slug).first()
            if bucket and hasattr(bucket, "gateway"):
                gateway_url = reverse("vault:gateway_page", kwargs={"bucket_slug": bucket.slug})
        context["gateway_url"] = gateway_url

        context["gateways"] = [
            {
                "name": gw.bucket.name,
                "url": reverse("vault:gateway_page", kwargs={"bucket_slug": gw.bucket.slug}),
            }
            for gw in FileGateway.objects.select_related("bucket").all()
        ]

        return PageProcessor().decorate(context, self.request)


class VaultFileDownloadView(View):
    """
    Download a file; respects public/private visibility.
    """
    def get(self, request, bucket_slug, key):
        file_obj = get_object_or_404(
            VaultFile.objects.select_related("bucket"),
            bucket__slug=bucket_slug,
            key=key
        )

        # Public file: anyone can download
        if file_obj.is_public:
            return FileResponse(
                file_obj.file.open(),
                as_attachment=True,
                filename=file_obj.file.name
            )

        # Private file: require login
        if not request.user.is_authenticated:
            return HttpResponseForbidden("You must be logged in to access this file.")

        return FileResponse(
            file_obj.file.open(),
            as_attachment=True,
            filename=file_obj.file.name
        )


class FileGatewayPageView(LoginRequiredMixin, DetailView):
    model = FileGateway
    template_name = "vault/gateway.html"
    context_object_name = "gateway"

    def get_object(self):
        bucket_slug = self.kwargs.get("bucket_slug")
        return get_object_or_404(FileGateway, bucket__slug=bucket_slug)

    def get(self, request, *args, **kwargs):
        gateway = self.get_object()
        # Access control
        if gateway.allowed_users.exists() and request.user not in gateway.allowed_users.all():
            return HttpResponseForbidden("You are not allowed to access this gateway")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return PageProcessor().decorate(context, self.request)


class FileGatewayUploadView(LoginRequiredMixin, View):
    """
    Handle uploads to a bucket through a gateway.
    """
    def post(self, request, bucket_slug):
        gateway = get_object_or_404(FileGateway, bucket__slug=bucket_slug)

        # Access control
        if gateway.allowed_users.exists() and request.user not in gateway.allowed_users.all():
            return JsonResponse({"error": "You are not allowed to use this gateway"}, status=403)

        if "file" not in request.FILES:
            return JsonResponse({"error": "No file uploaded"}, status=400)

        uploaded_file = request.FILES["file"]

        if uploaded_file.size > gateway.max_file_size * 1024:
            return JsonResponse({
                "error": f"File too large ({uploaded_file.size / (1024*1024):.1f} MB). Max is {gateway.max_file_size / 1024:.1f} MB."
            }, status=400)

        mime, _ = mimetypes.guess_type(uploaded_file.name)
        file_type = VaultFile.detect_type(mime)

        vault_file = VaultFile(
            owner=request.user,
            title=uploaded_file.name,
            file=uploaded_file,
            file_type=file_type,
            bucket=gateway.bucket,
            is_public=gateway.make_public
        )
        vault_file.save()
        vault_file.content_hash = vault_file.create_hash()
        vault_file.save()

        return JsonResponse({
            "result": {
                "title": vault_file.title,
                "key": vault_file.key,
                "bucket": gateway.bucket.slug,
                "file_type": vault_file.file_type,
                "public_url": vault_file.get_public_url(),
                "size": f"{uploaded_file.size / (1024*1024):.1f} MB",
            }
        })