from django.contrib import admin, messages
from django import forms
from .models import LatexProject, TexFile
from .batch import BatchAction
from django.core.files.base import ContentFile
from django_ace import AceWidget


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
