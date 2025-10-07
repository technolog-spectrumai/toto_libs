from django.views.generic import DetailView, ListView
from .models import Department, Document
from .mixins import PageDecoratedMixin
from documents.models import LatexPreset


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
# 📄 Document Detail View (Preset-aware rendering)
# ────────────────────────────────────────────────

class DocumentDetailView(PageDecoratedMixin, DetailView):
    model = Document
    template_name = 'documents/document_detail.html'
    context_object_name = 'document'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        document = self.object

        preview_html = ""  # or escape(document.content) if you want to show raw LaTeX safely
        preview_css = ""

        context.update({
            'preview': preview_html,
            'preview_css': preview_css,
            'department': document.department,
            'tags': document.tags.all(),
            'is_html': False
        })
        return context
