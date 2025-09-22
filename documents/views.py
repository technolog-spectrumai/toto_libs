from django.views.generic import DetailView, ListView
from django.shortcuts import get_object_or_404
from .models import Department, Document, HTMLFile
from .mixins import PageDecoratedMixin

# ────────────────────────────────────────────────
# 📄 Document List View
# ────────────────────────────────────────────────

class DocumentListView(PageDecoratedMixin, ListView):
    model = Document
    template_name = 'documents/document_list.html'
    context_object_name = 'documents'

    def get_queryset(self):
        queryset = super().get_queryset().select_related('department', 'created_by')
        department_id = self.request.GET.get('department')
        if department_id:
            queryset = queryset.filter(department_id=department_id)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'departments': Department.objects.all(),
            'selected_department': self.request.GET.get('department')
        })
        return context

# ────────────────────────────────────────────────
# 📄 Document Detail View (HTML Preview Only)
# ────────────────────────────────────────────────

class DocumentDetailView(PageDecoratedMixin, DetailView):
    model = Document
    template_name = 'documents/document_detail.html'
    context_object_name = 'document'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        html_file = getattr(self.object, 'html_file', None)

        preview_html = html_file.content if html_file and html_file.content else ""
        preview_css = ""

        if html_file and html_file.preset and html_file.preset.style_mapping:
            preview_css = " ".join(html_file.preset.style_mapping.values())

        context.update({
            'preview': preview_html,
            'preview_css': preview_css,
            'department': self.object.department,
            'tags': self.object.tags.all(),
        })
        return context

