"""RecoveryTicket in the admin — a WINDOW, deliberately not a workbench.

The profile card is where an approver acts, and the staff QUEUE (tickets with
no personal approver) renders only on a staff member's own profile — a page
some staff never visit. This registration is the safety net for that gap:
somewhere any admin can list open tickets, see who is waiting and since when,
and go act on their profile.

Approving is NOT offered here on purpose: approval mints a one-time link that
must be shown exactly once to a person who then carries it, and the admin's
save-a-row model has no way to do that honestly. Every custody-bearing field
is read-only; what an admin can do is look, and delete a row that should
never have existed.
"""
from django.contrib import admin

from .models import RecoveryTicket


@admin.register(RecoveryTicket)
class RecoveryTicketAdmin(admin.ModelAdmin):
    list_display = ("user", "status", "approver_rule", "approver",
                    "requested_at", "request_expires_at", "link_expires_at")
    list_filter = ("status", "approver_rule")
    search_fields = ("user__username", "approver__username")
    ordering = ("-requested_at",)
    date_hierarchy = "requested_at"

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        # Tickets are filed by the anonymous request flow; a hand-made one
        # would skip the approver-resolution audit trail entirely.
        return False
