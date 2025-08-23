from django.contrib import admin
from django import forms
from django_tiptap.widgets import TipTapWidget
from nested_admin import NestedModelAdmin, NestedStackedInline
from .models import Tag, Department, Document, Section, SubSection, Image, Diagram
from adminsortable2.admin import SortableAdminMixin
from django.utils.safestring import mark_safe


SHOW_INLINE = True
SHOW_TABLE = True

# 🏷️ Tags
admin.site.register(Tag)

# 🏢 Departments
@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ['name', 'owner']
    search_fields = ['name']
    list_filter = ['owner']


# ✏️ Forms
class SectionAdminForm(forms.ModelForm):
    content = forms.CharField(widget=TipTapWidget())

    class Meta:
        model = Section
        fields = '__all__'


class SectionInlineForm(forms.ModelForm):
    content = forms.CharField(widget=TipTapWidget())

    class Meta:
        model = Section
        fields = '__all__'


class SubSectionInlineForm(forms.ModelForm):
    content = forms.CharField(widget=TipTapWidget())

    class Meta:
        model = SubSection
        fields = '__all__'


# 📑 Inlines
class SubSectionInline(NestedStackedInline):
    model = SubSection
    form = SubSectionInlineForm
    extra = 1
    ordering = ['order']


if SHOW_INLINE:
    class SectionInline(NestedStackedInline):  # Use NestedStackedInline for nesting
        model = Section
        form = SectionInlineForm
        extra = 1
        ordering = ['order']
        inlines = [SubSectionInline]
else:
    SectionInline = None


# 📄 Document Admin
@admin.register(Document)
class DocumentAdmin(NestedModelAdmin):
    list_display = ['title', 'type', 'status', 'department', 'author', 'created_at']
    list_filter = ['type', 'status', 'department', 'tags']
    search_fields = ['title', 'summary', 'slug']
    readonly_fields = ['created_at']
    date_hierarchy = 'created_at'
    prepopulated_fields = {'slug': ('title',)}
    filter_horizontal = ['tags']
    inlines = [inline for inline in [SectionInline] if inline]

if SHOW_TABLE:
    @admin.register(Section)
    class SectionAdmin(SortableAdminMixin, admin.ModelAdmin):
        form = SectionAdminForm
        list_display = ['report', 'order', 'heading', 'created_at']
        list_filter = ['report', 'created_at']
        search_fields = ['heading', 'content']


if SHOW_TABLE:
    @admin.register(SubSection)
    class SubSectionAdmin(SortableAdminMixin, admin.ModelAdmin):
        form = SubSectionInlineForm
        list_display = ['section', 'order', 'title', 'created_at']
        list_filter = ['section']
        search_fields = ['title', 'content']


@admin.register(Image)
class ImageAdmin(admin.ModelAdmin):
    list_display = ['title', 'description', 'uploaded_at']
    search_fields = ['title', 'description']
    list_filter = ['uploaded_at']


@admin.register(Diagram)
class DiagramAdmin(admin.ModelAdmin):
    list_display = ['title', 'created_at']
    readonly_fields = ['created_at', 'render_mermaid_preview']
    fields = ['title', 'description', 'code', 'render_mermaid_preview']  # exclude created_at here
    search_fields = ['title', 'description', 'code']
    list_filter = ['created_at']
    ordering = ['-created_at']

    def render_mermaid_preview(self, obj):
        if not obj.code:
            return "No Mermaid code provided."
        return mark_safe(f"""
            <div class="mermaid" style="background:#f9f9f9;padding:1em;border-radius:6px;">
                {obj.code}
            </div>
        """)
    render_mermaid_preview.short_description = "Diagram Preview"

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


