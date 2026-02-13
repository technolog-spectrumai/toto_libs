from django.views.generic import ListView, DetailView
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from .models import VaultFile, Bucket
from oya.page import PageProcessor


class PublicFileListView(ListView):
    model = VaultFile
    template_name = 'vault/public_file_list.html'
    context_object_name = 'files'
    paginate_by = 2  # Show 10 files per page

    def get_queryset(self):
        queryset = VaultFile.objects.filter(is_public=True).select_related('owner', 'bucket')
        bucket_name = self.request.GET.get('bucket')
        if bucket_name:
            queryset = queryset.filter(bucket__name=bucket_name)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['buckets'] = Bucket.objects.all()
        context['selected_bucket'] = self.request.GET.get('bucket', '')
        decorated_context = PageProcessor().decorate(context, self.request)
        return decorated_context




class PublicFileDownloadView(DetailView):
    model = VaultFile

    def get(self, request, *args, **kwargs):
        bucket_name = kwargs.get('bucket')
        key = kwargs.get('key')
        file_obj = get_object_or_404(
            VaultFile.objects.select_related('bucket'),
            bucket__name=bucket_name,
            key=key,
            is_public=True
        )
        return FileResponse(file_obj.file.open(), as_attachment=True, filename=file_obj.file.name)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        decorated_context = PageProcessor().decorate(context, self.request)
        return decorated_context
