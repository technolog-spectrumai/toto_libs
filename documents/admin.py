from django import forms
from django.contrib import admin, messages
from django.utils.html import mark_safe
from django.utils.timezone import now
from django.core.files import File
from django_tiptap.widgets import TipTapWidget
from nested_admin import NestedModelAdmin, NestedStackedInline
from polymorphic.admin import PolymorphicParentModelAdmin, PolymorphicChildModelAdmin
from adminsortable2.admin import SortableAdminMixin
from .models import (
    Tag, Department, Document, HtmlDocument, LatexDocument,
    Section, HTMLSubSection, LaTeXSubSection, Image, Diagram, Formula
)
import os
from django_ace import AceWidget
from .tasks import convert_html_to_latex_task, convert_latex_to_html_task


SHOW_INLINE = True
SHOW_TABLE = False


admin.site.register(Tag)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ['name', 'owner']
    search_fields = ['name']
    list_filter = ['owner']


class SectionForm(forms.ModelForm):
    class Meta:
        model = Section
        fields = '__all__'


class HTMLSubSectionForm(forms.ModelForm):
    content = forms.CharField(widget=TipTapWidget())

    class Meta:
        model = HTMLSubSection
        fields = '__all__'


class LaTeXSubSectionForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = kwargs.get('instance')
        theme = 'chrome'
        if instance and instance.use_light_mode is False:
            theme = 'monokai'
        self.fields['content'].widget = AceWidget(mode='latex', theme=theme)

    class Meta:
        model = LaTeXSubSection
        fields = '__all__'


class HTMLSubSectionInline(NestedStackedInline):
    model = HTMLSubSection
    form = HTMLSubSectionForm
    extra = 1
    ordering = ['order']


class LaTeXSubSectionInline(NestedStackedInline):
    model = LaTeXSubSection
    form = LaTeXSubSectionForm
    extra = 1
    ordering = ['order']


class HtmlSectionInline(NestedStackedInline):
    model = Section
    form = SectionForm
    extra = 1
    ordering = ['order']
    inlines = [HTMLSubSectionInline]


class LatexSectionInline(NestedStackedInline):
    model = Section
    form = SectionForm
    extra = 1
    ordering = ['order']
    inlines = [LaTeXSubSectionInline]


class HtmlDocumentAdmin(PolymorphicChildModelAdmin, NestedModelAdmin):
    base_model = HtmlDocument
    inlines = [HtmlSectionInline]


class LatexDocumentAdmin(PolymorphicChildModelAdmin, NestedModelAdmin):
    base_model = LatexDocument
    inlines = [LatexSectionInline]


@admin.register(Document)
class DocumentAdmin(PolymorphicParentModelAdmin):
    base_model = Document
    child_models = (HtmlDocument, LatexDocument)
    list_display = ['title', 'slug', 'author', 'status', 'created_at', 'department', 'document_type']
    list_filter = ['title', 'status', 'author', 'department']
    readonly_fields = ['created_at']
    filter_horizontal = ['tags']
    actions = ['convert_to_latex', 'convert_to_html']

    @staticmethod
    def _convert(direction, queryset):
        conversion_map = {
            LatexDocument.get_type(): {
                "task": convert_html_to_latex_task,
                "target": LatexDocument.get_type(),
                "source": HtmlDocument.get_type()
            },
            HtmlDocument.get_type(): {
                "task": convert_latex_to_html_task,
                "target": HtmlDocument.get_type(),
                "source": LatexDocument.get_type()
            }
        }

        config = conversion_map[direction]
        converted = 0
        skipped = 0

        for doc in queryset:
            if doc.document_type == config["target"]:
                skipped += 1
            elif doc.document_type == config["source"]:
                config["task"].delay(doc.id)
                converted += 1

        msg_done = f"{converted} document(s) scheduled for {config['target']} conversion."
        msg_skip = f"{skipped} already in {config['target']} format."
        return f"{msg_done} {msg_skip}"

    def convert_to_latex(self, request, queryset):
        msg = self._convert(LatexDocument.get_type(), queryset)
        self.message_user(request, msg, messages.INFO)
    convert_to_latex.short_description = "Convert selected documents to LaTeX"

    def convert_to_html(self, request, queryset):
        msg = self._convert(HtmlDocument.get_type(), queryset)
        self.message_user(request, msg, messages.INFO)
    convert_to_html.short_description = "Convert selected documents to HTML"


admin.site.register(HtmlDocument, HtmlDocumentAdmin)
admin.site.register(LatexDocument, LatexDocumentAdmin)


if SHOW_TABLE:
    @admin.register(HTMLSubSection)
    class HTMLSubSectionAdmin(SortableAdminMixin, admin.ModelAdmin):
        form = HTMLSubSectionForm
        list_display = ['section', 'order', 'title', 'created_at']
        list_filter = ['section']
        search_fields = ['title', 'content']

    @admin.register(LaTeXSubSection)
    class LaTeXSubSectionAdmin(SortableAdminMixin, admin.ModelAdmin):
        form = LaTeXSubSectionForm
        list_display = ['section', 'order', 'title', 'created_at']
        list_filter = ['section']
        search_fields = ['title', 'content']


    class SectionAdminForm(forms.ModelForm):
        content = forms.CharField(widget=TipTapWidget())

        class Meta:
            model = Section
            fields = '__all__'

    @admin.register(Section)
    class SectionAdmin(SortableAdminMixin, admin.ModelAdmin):
        form = SectionAdminForm
        list_display = ['document', 'order', 'heading', 'created_at']
        list_filter = ['document', 'created_at']
        search_fields = ['heading', 'content']


@admin.register(Image)
class ImageAdmin(admin.ModelAdmin):
    list_display = ['title', 'description', 'uploaded_at']
    search_fields = ['title', 'description']
    list_filter = ['uploaded_at']


@admin.register(Diagram)
class DiagramAdmin(admin.ModelAdmin):
    list_display = ['title', 'created_at']
    readonly_fields = ['created_at', 'render_mermaid_preview']
    fields = ['title', 'description', 'code', 'render_mermaid_preview']
    search_fields = ['title', 'description', 'code']
    list_filter = ['created_at']
    ordering = ['-created_at']
    actions = ['convert_to_image']

    def render_mermaid_preview(self, obj):
        if not obj.code:
            return "No Mermaid code provided."
        return mark_safe(f"""
            <div class="mermaid" style="background:#f9f9f9;padding:1em;border-radius:6px;">
                {obj.code}
            </div>
        """)
    render_mermaid_preview.short_description = "Diagram Preview"

    def convert_to_image(self, request, queryset):
        count = 0
        for diagram in queryset:
            timestamp = now().strftime("%Y%m%d_%H%M%S")
            filename = f"{diagram.id}_{timestamp}.png"
            try:
                output_path = diagram.render_image(output_format='png')

                # Create Image model instance
                with open(output_path, 'rb') as f:
                    image_instance = Image.objects.create(
                        title=f"Diagram: {diagram.title}",
                        description=diagram.description,
                        file=File(f, name=filename)
                    )
                count += 1

            except Exception as e:
                self.message_user(request, f"Failed to render diagram '{diagram.title}': {e}", level='error')

        self.message_user(request, f"Successfully rendered and saved {count} diagram(s) as image(s).")

    convert_to_image.short_description = "Convert selected diagrams to image"

    class Media:
        js = [
            "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js",
        ]
        custom_js = """
        <script>
          document.addEventListener("DOMContentLoaded", function() {
            if (window.mermaid) {
              mermaid.initialize({ startOnLoad: true });
            }
          });
        </script>
        """
        def render(self):
            return mark_safe('\n'.join([f'<script src="{js}"></script>' for js in self.js]) + self.custom_js)


@admin.register(Formula)
class FormulaAdmin(admin.ModelAdmin):
    list_display = ['title', 'created_at']
    readonly_fields = ['created_at', 'render_latex_preview']
    fields = ['title', 'description', 'latex_code', 'render_latex_preview']
    search_fields = ['title', 'description', 'latex_code']
    list_filter = ['created_at']
    ordering = ['-created_at']
    actions = ['convert_to_image']

    def render_latex_preview(self, obj):
        if not obj.latex_code:
            return "No LaTeX code provided."
        return mark_safe(f"""
            <div style="background:#f9f9f9;padding:1em;border-radius:6px;">
                <p>\

\[{obj.latex_code}\\]

</p>
            </div>
        """)
    render_latex_preview.short_description = "LaTeX Preview"

    class Media:
        js = [
            "https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js",
        ]

    def convert_to_image(self, request, queryset):
        count = 0
        for formula in queryset:
            try:
                output_path= formula.render_image()
                filename = os.path.basename(output_path)
                with open(output_path, 'rb') as f:
                    Image.objects.create(
                        title=f"Formula: {formula.title}",
                        description=formula.description,
                        file=File(f, name=filename) # fix
                    )
                count += 1
            except Exception as e:
                self.message_user(request, f"Failed to render formula '{formula.title}': {e}", level='error')
        self.message_user(request, f"Successfully rendered {count} formula(s) to image.")
    convert_to_image.short_description = "Convert selected formulas to image"



