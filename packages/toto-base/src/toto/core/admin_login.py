"""Django's admin login, saying why when the sign-in lockout refused (2026-09-30).

The lockout itself needs nothing from the admin: ``AdminAuthenticationForm``
calls ``authenticate(self.request, ...)`` like every other door, and the
backend refuses there. Without this form the refusal would read "Please enter
the correct username and password for a staff account" — true of nothing, and
an invitation to keep typing. ``CoreConfig.ready`` sets it as the default
admin site's ``login_form`` unless a host has chosen its own.

A member who is not staff and types the RIGHT password is turned away here
after the comparison. The lockout counts a try before it is compared
(``begin_try``, 2026-10-02), so that try gives its count back
(``release_try``, 41.5): it was no guess, and a dozen visits to /admin/
paused the member's own sign-in everywhere for 15 minutes.
"""

from django.contrib.admin.forms import AdminAuthenticationForm
from django.core.exceptions import ValidationError

from toto.core.signin_lockout import refusal_for, release_try


class SigninLockoutAdminAuthenticationForm(AdminAuthenticationForm):
    def clean(self):
        try:
            return super().clean()
        except ValidationError:
            held = refusal_for(self.request)
            if held is not None:
                raise ValidationError(held.message, code="signin_paused") from None
            if self.get_user() is not None:
                release_try(self.request)
            raise
