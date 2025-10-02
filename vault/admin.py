from django.contrib import admin
from .models import KeyRing, VaultPdf

@admin.register(KeyRing)
class KeyRingAdmin(admin.ModelAdmin):
    list_display = ('label', 'owner', 'created_at')
    search_fields = ('label', 'owner__username')
    list_filter = ('created_at',)


@admin.register(VaultPdf)
class VaultPdfAdmin(admin.ModelAdmin):
    list_display = ('title', 'owner', 'uploaded_at', 'is_encrypted')
    search_fields = ('title', 'owner__username')
    list_filter = ('is_encrypted', 'uploaded_at')
