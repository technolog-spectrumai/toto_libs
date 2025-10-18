from django.contrib import admin
from .models import KeyRing


@admin.register(KeyRing)
class KeyRingAdmin(admin.ModelAdmin):
    list_display = ('label', 'owner', 'created_at')
    search_fields = ('label', 'owner__username')
    readonly_fields = ('salt', 'created_at')
    list_filter = ('created_at',)

