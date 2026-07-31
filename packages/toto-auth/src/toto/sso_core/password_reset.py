"""The password-reset flow, shared by both federated modes.

It used to live in ``sso_master``, which meant only a *provider* host had it: a
consumer mounted ``sso_client.urls``, ``sso:password_reset`` did not reverse, and
``toto.core.auth_views._password_reset_url`` correctly hid the link. That was
right while a consumer had no local accounts — every account was the provider's,
and resetting a password there is the provider's job. It stops being right the
moment a consumer host has accounts of its own, because those users have nowhere
to go.

So it lives in ``sso_core``, the one auth app installed in **both** federated
modes (``auth_config.auth_apps``), and both urlconfs mount the same four names.
Nothing in ``toto-base`` changed: ``_password_reset_url()`` already reverses
``sso:password_reset`` and already degrades on ``NoReverseMatch``.

Two properties worth naming, because neither needed code:

* **Reset can only ever reach a local account.** Django's
  ``PasswordResetForm.get_users()`` filters on ``has_usable_password()``, and an
  account provisioned for a federated identity gets ``set_unusable_password()``
  (``sso_client.views``). So a federated user asking for a reset is told an email
  was sent and none is, which is the standard non-enumerating behaviour.
* **No delivering email backend means the flow is not offered at all** rather
  than accepting a request and dropping the mail. Both this module and the
  ``password_reset_available`` context flag check the same
  ``email_delivery_configured()``.

Local mode (``TOTO_AUTH_MODE=local``) still has no reset: it installs neither
``sso_core`` nor any OIDC app, and its ``sso`` namespace is the two aliases in
``toto.auth_local_urls``. That is unchanged by this move.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordResetForm, SetPasswordForm
from django.contrib.auth.tokens import default_token_generator
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode

from toto.core.email_config import email_delivery_configured
from toto.ui import PageProcessor


def password_reset_view(request):
    processor = PageProcessor()

    # Without a delivering email backend the reset email would silently go nowhere.
    # Don't accept the request and drop the mail, and don't bounce to login with no
    # explanation either: render the page in its unavailable state — no form, a plain
    # apology, and nothing is sent. The "Forgot password?" link is already hidden in
    # this case (see auth_views.password_reset_available), so this is reached only by
    # a direct URL or a stale bookmark, which is exactly when the apology helps.
    if not email_delivery_configured():
        context = {"unavailable": True, "page_title": "Password Reset Unavailable"}
        return render(request, "sso/password_reset.html", processor.decorate(context, request))

    form = PasswordResetForm(request.POST or None)
    context = {"form": form, "page_title": "Reset Password"}

    if request.method == "POST" and form.is_valid():
        form.save(
            request=request,
            use_https=request.is_secure(),
            email_template_name="sso/password_reset_email.html",
            subject_template_name="sso/password_reset_subject.txt",
            extra_email_context=None,
        )
        return redirect(reverse("sso:password_reset_done"))

    return render(request, "sso/password_reset.html", processor.decorate(context, request))


def password_reset_done_view(request):
    processor = PageProcessor()
    context = {"page_title": "Check Your Email"}
    return render(request, "sso/password_reset_done.html", processor.decorate(context, request))


def password_reset_confirm_view(request, uidb64, token):
    processor = PageProcessor()
    User = get_user_model()

    user = None
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        pass

    valid = user is not None and default_token_generator.check_token(user, token)
    form = SetPasswordForm(user, request.POST or None) if valid else None
    context = {"form": form, "validlink": valid, "page_title": "Set New Password"}

    if request.method == "POST" and valid and form.is_valid():
        form.save()
        return redirect(reverse("sso:password_reset_complete"))

    return render(request, "sso/password_reset_confirm.html", processor.decorate(context, request))


def password_reset_complete_view(request):
    processor = PageProcessor()
    context = {"page_title": "Password Reset Complete"}
    return render(request, "sso/password_reset_complete.html", processor.decorate(context, request))


def urlpatterns(prefix: str = ""):
    """The four reset routes, for whichever urlconf serves the ``sso`` namespace.

    A function rather than a module-level list because the two urlconfs are
    mounted at different depths and must still produce the same four *names*:
    ``sso_master.urls`` is included at ``""`` and carries its own ``sso/``
    segment, while ``sso_client.urls`` is included at ``"sso/"`` and must not
    repeat it. Pass the prefix the caller needs; the names are defined once here,
    so they cannot drift between provider and consumer.
    """
    from django.urls import path

    base = f"{prefix}password-reset/"
    return [
        path(base, password_reset_view, name="password_reset"),
        path(f"{base}done/", password_reset_done_view, name="password_reset_done"),
        path(f"{base}<uidb64>/<token>/", password_reset_confirm_view,
             name="password_reset_confirm"),
        path(f"{base}complete/", password_reset_complete_view,
             name="password_reset_complete"),
    ]
