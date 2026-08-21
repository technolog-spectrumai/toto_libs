"""Read-mostly admin. Reserving and mounting are user acts on the Gear page.

Nothing here can create a lease: a reservation that skipped
``services.reserve`` would skip pool admission, and an over-booked pool is not
a state the arithmetic can recover from by itself.
"""

from django.contrib import admin

from .models import ComputeLease, Execution, GearEvent, GearRuntime, PoolGuard


@admin.register(ComputeLease)
class ComputeLeaseAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "cpu_millicores", "ram_mb", "scratch_mb",
                    "pids", "expires_at", "released_at")
    list_filter = ("released_at",)
    search_fields = ("name", "uuid", "owner__username")
    readonly_fields = ("uuid", "created_at")

    def has_add_permission(self, request):
        return False


@admin.register(GearRuntime)
class GearRuntimeAdmin(admin.ModelAdmin):
    list_display = ("lease", "state", "state_at", "sampled_at",
                    "manager_generation")
    list_filter = ("state",)

    def has_add_permission(self, request):
        return False


@admin.register(Execution)
class ExecutionAdmin(admin.ModelAdmin):
    list_display = ("uuid", "operation", "family", "status", "lease",
                    "served_warm", "created_at", "finished_at")
    list_filter = ("status", "family", "served_warm")
    search_fields = ("uuid", "subject_label", "subject_id")
    readonly_fields = ("uuid", "created_at")

    def has_add_permission(self, request):
        return False


@admin.register(GearEvent)
class GearEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "lease", "kind", "accepted", "refusal_code")
    list_filter = ("kind", "accepted")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        # The model refuses an edit anyway; saying so here keeps the admin from
        # offering a form that can only fail.
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(PoolGuard)
