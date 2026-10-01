from django.contrib.auth import get_user_model
from toto.core.models import Platform
from django.shortcuts import render, redirect, get_object_or_404
from toto.people.models import Person
from toto.socialhub import applications
from toto.socialhub.models import (Community, MembershipApplication,
                                   ReferenceRequest, generate_code)
from toto.ui import PageProcessor
from django.utils import timezone
from django.contrib.auth.models import User
from toto.socialhub.forms import MembershipApplicationForm, CodeVerificationForm, ReferenceRequestForm
from toto.socialhub.captcha import generate_code_captcha
from django.core.mail import EmailMessage
from toto.core.auth_cooldown import (
    captcha_retry_cooldown_remaining,
    captcha_retry_cooldown_seconds,
    clear_captcha_retry_cooldown,
    start_captcha_retry_cooldown,
)
import logging
from django.core.exceptions import PermissionDenied
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _


logger = logging.getLogger(__name__)
User = get_user_model()

# The purpose tag toto.jess reads off the message and pops. On a host without jess the
# header is simply carried through to SMTP, so this costs nothing there.
_JESS_PURPOSE_HEADER = "X-Jess-Purpose"

#: The applications this browser verified, by id, each with the moment it was
#: verified (2026-10-01, the review of stage 35). The reference step — the
#: referrer, the message and the password — opens only for them: before,
#: anybody holding an application's id, a number counted up from 1, could set
#: the password of a pending applicant's account and read their address.
VERIFIED_SESSION_KEY = "socialhub_verified_applications"


def _remember_verified(request, application):
    verified = dict(request.session.get(VERIFIED_SESSION_KEY) or {})
    verified[str(application.pk)] = application.verified_at.isoformat()
    request.session[VERIFIED_SESSION_KEY] = verified


def _verified_here(request, application_id):
    """The application, when this browser's session verified it; else None.

    The session is asked first, so an id this browser never verified gets
    the same answer whether or not it exists. The moment must still be the
    application's own: a renewal (``applications.renew``) clears
    ``verified_at``, so the browser that verified an earlier round — maybe
    not the applicant's — no longer counts once the address applied again.
    """
    stamp = (request.session.get(VERIFIED_SESSION_KEY) or {}).get(str(application_id))
    when = parse_datetime(stamp) if isinstance(stamp, str) else None
    if when is None:
        return None
    application = get_object_or_404(MembershipApplication, pk=application_id)
    return application if application.verified_at == when else None


def _not_this_browser(request, processor, application_id):
    """The reference step's refusal: a sentence, no form, no address, nothing set."""
    logger.warning(f"Reference step refused for application ID '{application_id}': "
                   f"not verified in this browser.")
    context = {
        "page_title": "Endorse Application",
        "refusal": _("Only the browser in which this application's code was typed can "
                     "ask for its references. If that is no longer possible, apply again "
                     "with the same e-mail address once the application has lapsed."),
    }
    return render(request, "socialhub/reference_request.html",
                  processor.decorate(context, request), status=403)


def _send_endorsement_mail(subject, body, *, to, community):
    """Send an endorsement decision email through whatever the platform's backend is.

    This replaces ``api.EmailService.send_email``, which was never able to run: it
    declared ``smtp_password`` keyword-only and both call sites here omitted it, so every
    call raised ``TypeError`` into the ``except Exception`` below and was logged as
    "Failed to send approval email". Endorsement mail has never actually been delivered.

    ``reply_to`` is the community's own address rather than its own SMTP relay — the
    useful half of the per-community sender identity that went away with EmailService,
    without a second place to keep a credential.

    ``fail_silently=True`` keeps the promise the old ``except Exception`` was making:
    accepting a reference must not fail because mail did.
    """
    msg = EmailMessage(
        subject=subject,
        body=body,
        to=[to],
        reply_to=[community.email] if getattr(community, "email", "") else [],
        headers={_JESS_PURPOSE_HEADER: "socialhub_endorsement"},
    )
    msg.send(fail_silently=True)


def membership_application_view(request):
    processor = PageProcessor()
    form = MembershipApplicationForm(request.POST or None)
    context = {"form": form, "page_title": "Apply for Membership",
               "privacy_notice": form.privacy_notice}

    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        community = form.cleaned_data["community"]
        username = form.cleaned_data["username"]
        notice = form.privacy_notice

        if form.renewing is not None:
            # An earlier application with this address lapsed before its
            # applicant got in (2026-10-01): it starts over — a new code, a
            # new week, the account it made named as typed now — instead of
            # the address being refused and the old code answering "expired"
            # for ever. One whose every reference was declined starts over
            # the same way. See toto.socialhub.applications.
            application = applications.renew(form.renewing, username=username,
                                              community=community, notice=notice)
            from toto.socialhub import audit

            audit.notice_accepted(application)
            logger.info(f"Lapsed application renewed for '{email}' (username '{username}').")
            return redirect("socialhub:application_success", username=email)

        # The login username is chosen by the applicant (validated unique in the
        # form); the email stays the stable key for the application + verification
        # steps below. The account stays inactive until a reference is accepted.
        user, _ = User.objects.get_or_create(
            username=username, defaults={"email": email, "is_active": False}
        )
        application, created = MembershipApplication.objects.get_or_create(
            email=email,
            defaults={
                "community": community,
                "expires_at": timezone.now() + timezone.timedelta(days=applications.LIFETIME_DAYS),
                "status": "pending",
                # The notice the form showed and the applicant ticked
                # (2026-10-01); carried to the Person on admission.
                "privacy_version": notice.version,
                "privacy_accepted_at": timezone.now(),
            }
        )

        if created:
            application.code = generate_code()
            application.save()
            from toto.socialhub import audit

            audit.notice_accepted(application)
            logger.info(f"New application created for '{email}' (username '{username}') with code '{application.code}'.")
        else:
            logger.info(f"Existing application reused for '{email}'.")

        # The success/verification URLs are keyed by email (their stable identifier).
        return redirect("socialhub:application_success", username=email)

    return render(request, "socialhub/membership_application.html", processor.decorate(context, request))


def application_success_view(request, username):
    processor = PageProcessor()
    # The `username` URL slug is the applicant's email — the stable key for the
    # application + verification flow (the login username is chosen separately).
    # The verification code is never emailed: the verification page shows it as
    # a CAPTCHA the applicant retypes, and the reference/endorsement step is
    # what actually gates membership.
    email = username
    context = {"page_title": "Application Submitted", "username": email}

    logger.info(f"Application success page viewed for '{email}'.")
    return render(request, "socialhub/application_success.html", processor.decorate(context, request))


def verify_application_view(request, username):
    processor = PageProcessor()
    form = CodeVerificationForm(request.POST or None)
    context = {"form": form, "page_title": "Verify Application", "username": username}

    # The code is never mailed — it is shown as a distorted CAPTCHA the
    # applicant retypes to prove they are human. The reference/endorsement
    # step is what actually gates membership.
    application = MembershipApplication.objects.filter(email=username).first()
    captcha_mode = application is not None
    if captcha_mode:
        try:
            context["captcha_image"] = generate_code_captcha(application.code)
        except Exception as e:
            logger.error(f"Failed to render CAPTCHA for '{username}': {e}")

    # While the CAPTCHA is shown, rate-limit retries the same way login does:
    # a failed code starts a short cooldown during which the form is disabled.
    if request.method == "POST" and captcha_mode:
        remaining = captcha_retry_cooldown_remaining(request)
        if remaining > 0:
            context["error"] = f"Please wait {remaining} seconds before trying again."
            context["cooldown_remaining"] = remaining
            logger.warning(f"CAPTCHA retry blocked by cooldown for '{username}' ({remaining}s left).")
            return render(request, "socialhub/membership_verification.html", processor.decorate(context, request))

    if request.method == "POST" and form.is_valid():
        code = form.cleaned_data["code"]
        try:
            app = MembershipApplication.objects.get(code=code, email=username)
            if app.is_expired():
                # Since 2026-10-01 applying again renews a lapsed application
                # (applications.renew), so the answer says how.
                context["error"] = _("This code has expired. Apply again with the same "
                                     "e-mail address to get a new one.")
                logger.warning(f"Verification failed: code expired for '{username}'.")
            elif app.is_verified:
                context["message"] = "This application is already verified."
                logger.info(f"Verification skipped: already verified for '{username}'.")
            else:
                app.verified_at = timezone.now()
                app.status = "verified"
                app.save()
                # The reference step is this browser's from now on (2026-10-01).
                _remember_verified(request, app)
                if captcha_mode:
                    clear_captcha_retry_cooldown(request)
                logger.info(f"Verification successful for '{username}'.")
                return redirect("socialhub:reference_request", application_id=app.id)
        except MembershipApplication.DoesNotExist:
            context["error"] = "Invalid code or username."
            logger.warning(f"Verification failed: no application found for '{username}' with code '{code}'.")
            if captcha_mode:
                context["cooldown_remaining"] = captcha_retry_cooldown_seconds()
                start_captcha_retry_cooldown(request)

    return render(request,"socialhub/membership_verification.html", processor.decorate(context, request))


def verification_success_view(request):
    processor = PageProcessor()
    context = {"page_title": "Verification Complete"}
    logger.info(f"Verification success page viewed by user '{request.user.username}'.")
    return render(request, "socialhub/verification_success.html", processor.decorate(context, request))


def reference_request_view(request, application_id):
    processor = PageProcessor()
    # Only the browser that verified the code (2026-10-01): the page shows
    # the applicant's address and sets their account's password.
    application = _verified_here(request, application_id)
    if application is None:
        return _not_this_browser(request, processor, application_id)
    form = ReferenceRequestForm(request.POST or None, application=application)

    context = {
        "form": form,
        "page_title": "Endorse Application",
        "application": application,
    }

    if request.method == "POST" and form.is_valid():
        reference_request = form.save(commit=False)
        reference_request.application = application
        reference_request.referrer = form.cleaned_data["referrer"]
        reference_request.save()
        logger.info(f"Reference submitted by '{request.user.username}' for application ID '{application_id}'.")

        # Optional: capture the applicant's chosen password now. They stay INACTIVE
        # until a referrer accepts (see ReferenceRequest.save) — set_password here just
        # means they can log in directly once approved, instead of having to go through
        # the password-reset flow.
        password = form.cleaned_data.get("password")
        if password:
            # Only the account still waiting for its acceptance (2026-10-01):
            # this page is public, and the first account at the address used
            # to be taken — a member's, the applicant's own once admitted, or
            # anybody's whose address was typed on an application.
            applicant = applications.applicant_account(application, waiting_only=True)
            if applicant:
                applicant.set_password(password)
                applicant.save(update_fields=["password"])
                logger.info(f"Applicant '{application.email}' set a password during the endorsement request.")

        return redirect("socialhub:reference_next", application_id=application.id)

    return render(request, "socialhub/reference_request.html", processor.decorate(context, request))


def reference_next(request, application_id):
    processor = PageProcessor()
    # The thank-you page names the applicant's address too (2026-10-01).
    application = _verified_here(request, application_id)
    if application is None:
        return _not_this_browser(request, processor, application_id)
    context = {
        "application": application,
        "page_title": "Thank You for Your Endorsement",
    }
    logger.info(f"Reference thank-you page viewed for application ID '{application_id}' by user '{request.user.username}'.")
    return render(request, "socialhub/reference_next.html", processor.decorate(context, request))


@require_POST
@login_required
def reference_accept(request, ref_id):
    ref = get_object_or_404(ReferenceRequest, pk=ref_id)

    # Only the referrer can accept
    if ref.referrer.user != request.user:
        raise PermissionDenied(_("You cannot modify this reference request."))

    ref.status = "accepted"
    ref.responded_at = timezone.now()
    ref.save()  # triggers your model logic to activate the user

    # ---------------------------------------------------------
    # Send email to the applicant (they can now log in)
    # ---------------------------------------------------------
    try:
        application = ref.application
        user = User.objects.get(email=application.email)
        community = application.community

        subject = f"Your Membership in {community.name} Has Been Approved"
        body = (
            f"Hello,\n\n"
            f"Good news! Your reference has been accepted and your membership "
            f"in {community.name} is now fully approved.\n\n"
            f"You can now log in using your email address: {user.email}\n\n"
            f"Welcome aboard!\n"
            f"{community.name} Team"
        )
        _send_endorsement_mail(subject, body, to=user.email, community=community)
        logger.info(f"Approval email queued for '{user.email}'.")

    except Exception as e:
        logger.error(f"Failed to send approval email for reference '{ref_id}': {e}")

    return redirect("socialhub:profile_details", slug=request.user.community_profile.slug)



@require_POST
@login_required
def reference_reject(request, ref_id):
    ref = get_object_or_404(ReferenceRequest, pk=ref_id)

    # Only the referrer can reject
    if ref.referrer.user != request.user:
        raise PermissionDenied(_("You cannot modify this reference request."))

    ref.status = "declined"
    ref.responded_at = timezone.now()
    ref.save()

    # ---------------------------------------------------------
    # Send "sorry" email to the applicant
    # ---------------------------------------------------------
    try:
        application = ref.application
        user = User.objects.get(email=application.email)
        community = application.community

        subject = f"Your Membership Application to {community.name}"
        body = (
            f"Hello,\n\n"
            f"We're sorry to inform you that your reference for joining {community.name} "
            f"was not approved at this time.\n\n"
            f"This does not prevent you from applying again in the future.\n"
            f"If you believe this was a mistake or would like more information, "
            f"please contact the community leadership.\n\n"
            f"Best regards,\n"
            f"{community.name} Team"
        )
        _send_endorsement_mail(subject, body, to=user.email, community=community)
        logger.info(f"Rejection email queued for '{user.email}'.")

    except Exception as e:
        logger.error(f"Failed to send rejection email for reference '{ref_id}': {e}")

    return redirect("socialhub:profile_details", slug=request.user.community_profile.slug)



