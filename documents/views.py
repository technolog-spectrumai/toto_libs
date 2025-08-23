from django.views.generic import ListView, DetailView
from .models import Document, Section
from .mixins import PageDecoratedMixin

# 🏢 Offices (commented out for now)
# from .models import Office
# class OfficeListView(PageDecoratedMixin, ListView):
#     model = Office
#     template_name = 'document/office_list.html'
#     context_object_name = 'offices'

# class OfficeDetailView(PageDecoratedMixin, DetailView):
#     model = Office
#     template_name = 'document/office_detail.html'
#     context_object_name = 'office'

#     def get_context_data(self, **kwargs):
#         context = super().get_context_data(**kwargs)
#         office = self.get_object()
#         context['documents'] = office.documents.all()
#         return context

class DocumentListView(PageDecoratedMixin, ListView):
    model = Document
    template_name = 'document/document_list.html'
    context_object_name = 'documents'


class DocumentDetailView(PageDecoratedMixin, DetailView):
    model = Document
    template_name = 'document/document_detail.html'
    context_object_name = 'document'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        document = self.get_object()
        context['sections'] = document.sections.all() if document.type == 'Report' else None
        context['tags'] = document.tags.all()
        return context


# 📎 Sections (Report only)
class SectionDetailView(PageDecoratedMixin, DetailView):
    model = Section
    template_name = 'document/section_detail.html'
    context_object_name = 'section'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        section = self.get_object()
        context['report'] = section.report
        return context
