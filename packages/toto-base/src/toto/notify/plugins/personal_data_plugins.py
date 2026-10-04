"""A member's notifications in the copy of their data (2026-10-04).

Their own rows — what they were told, and when they read it — as
``toto.core.personal_data`` asks an app for its tables. The ``actor`` column
is left out: who did it is the other member's account, not theirs.
"""

from toto.core.personal_data import PersonalDataPlugin, Table, rows_of


@PersonalDataPlugin.plugin(key="notify")
class NotificationsData(PersonalDataPlugin):
    def tables(self, user):
        from toto.notify.models import Notification

        rows = rows_of(Notification.objects.filter(recipient=user).order_by("created", "pk"),
                       omit=("actor", "collapse_key"))
        return [Table("notifications",
                      "What the platform told you in the bell, and when you read it.",
                      rows)]
