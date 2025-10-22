from .models import LatexProject, TexFile
from django import forms
from django_ace import AceWidget
from django.core.files.base import ContentFile
from vault.models import VaultFile
from .models import LatexTemplate
from django.contrib import admin, messages
from django.db.models import JSONField
from django.utils.html import format_html
from django_json_widget.widgets import JSONEditorWidget
from .models import LatexGenerator
from .batch import BatchAction
import jsonschema


class LatexTemplateForm(forms.ModelForm):
    template_content = forms.CharField(
        widget=AceWidget(mode='latex', theme='chrome'),
        required=False,
        label="LaTeX Template Content"
    )

    class Meta:
        model = LatexTemplate
        fields = ['name', 'tex_template_file', 'template_content', 'json_schema', 'check_schema']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.tex_template_file:
            try:
                self.fields['template_content'].initial = self.instance.tex_template_file.read().decode('utf-8')
                self.instance.tex_template_file.seek(0)
            except Exception:
                self.fields['template_content'].initial = ""

    def save(self, commit=True):
        instance = super().save(commit=False)
        content = self.cleaned_data.get('template_content')

        if content:
            instance.tex_template_file.save(
                f"{instance.name}.tex",
                ContentFile(content.encode('utf-8')),
                save=False
            )

        if commit:
            instance.save()
        return instance

@admin.register(LatexTemplate)
class LatexTemplateAdmin(admin.ModelAdmin):
    form = LatexTemplateForm
    formfield_overrides = {
        JSONField: {'widget': JSONEditorWidget}
    }
    list_display = ['name', 'check_schema']
    search_fields = ['name']


@admin.register(LatexGenerator)
class LatexGeneratorAdmin(admin.ModelAdmin):
    formfield_overrides = {
        JSONField: {'widget': JSONEditorWidget}
    }
    actions = ['bake_selected_generators']
    readonly_fields = ['render_check', 'schema_check']

    def bake_selected_generators(self, request, queryset):
        def bake_one(generator):
            return generator.bake_to_texfile()

        result = BatchAction(queryset).run(bake_one)
        BatchAction.display_messages(result, self.message_user, request, verb="baked")

    bake_selected_generators.short_description = "Bake selected LaTeX generators to .tex files"

    def render_check(self, obj):
        try:
            obj.render_to_string()
            return format_html('<span style="color:green;">Rendered successfully</span>')
        except Exception as e:
            return format_html('<span style="color:red;">Render error - {}</span>', str(e))

    def schema_check(self, obj):
        try:
            if obj.template.check_schema and obj.template.json_schema:
                jsonschema.validate(instance=obj.json_data, schema=obj.template.json_schema)
            return format_html('<span style="color:green;">Valid</span>')
        except jsonschema.ValidationError as e:
            return format_html('<span style="color:red;">{}</span>', e.message)
        except Exception:
            return format_html('<span style="color:red;">Schema error</span>')

    list_display = ['filename', 'project', 'template', 'render_check', 'schema_check']
    search_fields = ['filename', 'project__name', 'template__name']
    list_filter = ['template']


class TexFileForm(forms.ModelForm):
    content = forms.CharField(
        widget=AceWidget(mode='latex', theme='chrome'),
        required=False,
        label="LaTeX Source"
    )

    class Meta:
        model = TexFile
        fields = ['project', 'filename', 'file']  # file is optional if content is provided

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Load file content into editor if file exists
        if self.instance and self.instance.file:
            try:
                self.fields['content'].initial = self.instance.file.read().decode('utf-8')
                self.instance.file.seek(0)
            except Exception:
                self.fields['content'].initial = ""

    def clean(self):
        cleaned_data = super().clean()
        file = cleaned_data.get('file')
        content = cleaned_data.get('content')

        if not file and not content:
            raise forms.ValidationError("You must provide either a file or LaTeX content.")

        filename = cleaned_data.get('filename', '')
        if not filename.lower().endswith('.tex'):
            self.add_error('filename', "Filename must end with .tex")

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        content = self.cleaned_data.get('content')

        # If content is provided, overwrite or create the file
        if content:
            instance.file.save(instance.filename, ContentFile(content.encode('utf-8')), save=False)

        if commit:
            instance.save()
        return instance

@admin.register(TexFile)
class TexFileAdmin(admin.ModelAdmin):
    form = TexFileForm
    list_display = ['filename', 'project', 'created_at']
    search_fields = ['filename', 'project__name']
    list_filter = ['created_at', 'project']

    # @admin.action(description="Compile selected LaTeX files (async)")
    # def compile_selected_texfiles(self, request, queryset):
    #     def compile_one(texfile):
    #         if not texfile.file:
    #             self.message_user(request, f"Skipped {texfile.filename} - no LaTeX file", messages.WARNING)
    #             return False
    #         texfile.compile()
    #         return True
    #
    #     result = BatchAction(queryset).run(compile_one)
    #     BatchAction.display_messages(result, self.message_user, request, verb="compiled")
    #
    # actions = [compile_selected_texfiles]


@admin.register(LatexProject)
class LatexProjectAdmin(admin.ModelAdmin):
    list_display = ['name', 'user', 'bucket', 'created_at']
    search_fields = ['name', 'user__username']
    list_filter = ['bucket', 'created_at']

    @admin.action(description="Compile all LaTeX files in selected projects")
    def compile_selected_projects(self, request, queryset):
        total = 0
        for project in queryset:
            compiled = project.compile_all()
            total += len(compiled)
        self.message_user(request, f"Compiled {total} LaTeX files.", messages.SUCCESS)

    actions = [compile_selected_projects]
