from django.contrib import admin
from .models import Resume, WorkExperience, Education, Distinction, Language, Skill

@admin.register(Resume)
class ResumeAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'email', 'phone_number', 'citizenship', 'created_at')
    search_fields = ('full_name', 'email', 'phone_number', 'citizenship')
    ordering = ('-created_at',)
    readonly_fields = ('created_at',)


@admin.register(WorkExperience)
class WorkExperienceAdmin(admin.ModelAdmin):
    list_display = ('title', 'company', 'location', 'start_date', 'end_date', 'resume')
    search_fields = ('title', 'company', 'location', 'resume__full_name')
    list_filter = ('company', 'location')
    autocomplete_fields = ('resume',)


@admin.register(Education)
class EducationAdmin(admin.ModelAdmin):
    list_display = ('degree', 'institution', 'specialization', 'start_date', 'end_date', 'resume')
    search_fields = ('degree', 'institution', 'specialization', 'resume__full_name')
    list_filter = ('institution',)
    autocomplete_fields = ('resume',)


@admin.register(Distinction)
class DistinctionAdmin(admin.ModelAdmin):
    list_display = ('title', 'date_awarded', 'resume')
    search_fields = ('title', 'resume__full_name')
    list_filter = ('date_awarded',)
    autocomplete_fields = ('resume',)


@admin.register(Language)
class LanguageAdmin(admin.ModelAdmin):
    list_display = ('name', 'proficiency', 'resume')
    search_fields = ('name', 'resume__full_name')
    list_filter = ('proficiency',)
    autocomplete_fields = ('resume',)


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = ('category', 'description', 'resume')
    search_fields = ('category', 'description', 'resume__full_name')
    list_filter = ('category',)
    autocomplete_fields = ('resume',)
