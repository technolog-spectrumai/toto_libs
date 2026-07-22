from django.contrib.auth import get_user_model
from toto.core.models import Platform
from django.shortcuts import render, redirect, get_object_or_404
from toto.people.models import Person
from toto.socialhub.models import Community, MembershipApplication, generate_code, ReferenceRequest
from toto.ui import PageProcessor
from django.utils import timezone
from django.contrib.auth.models import User
from toto.socialhub.forms import MembershipApplicationForm, CodeVerificationForm, ReferenceRequestForm
from toto.socialhub.captcha import resolve_email_service, generate_code_captcha
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


logger = logging.getLogger(__name__)
User = get_user_model()


def membership_application_view(request):
    processor = PageProcessor()
    form = MembershipApplicationForm(request.POST or None)
    context = {"form": form, "page_title": "Apply for Membership"}

    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        community = form.cleaned_data["community"]
        username = form.cleaned_data["username"]

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
                "expires_at": timezone.now() + timezone.timedelta(days=7),
                "status": "pending"
            }
        )

        if created:
            application.code = generate_code()
            application.save()
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
                context["error"] = "This code has expired."
                logger.warning(f"Verification failed: code expired for '{username}'.")
            elif app.is_verified:
                context["message"] = "This application is already verified."
                logger.info(f"Verification skipped: already verified for '{username}'.")
            else:
                app.verified_at = timezone.now()
                app.status = "verified"
                app.save()
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
    application = get_object_or_404(MembershipApplication, pk=application_id)
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
            applicant = User.objects.filter(email=application.email).first()
            if applicant:
                applicant.set_password(password)
                applicant.save(update_fields=["password"])
                logger.info(f"Applicant '{application.email}' set a password during the endorsement request.")

        return redirect("socialhub:reference_next", application_id=application.id)

    return render(request, "socialhub/reference_request.html", processor.decorate(context, request))


def reference_next(request, application_id):
    processor = PageProcessor()
    application = get_object_or_404(MembershipApplication, pk=application_id)
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
        raise PermissionDenied("You cannot modify this reference request.")

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

        svc = resolve_email_service(community)
        if svc is None:
            logger.info(f"No email service for '{community.name}'; skipping approval email.")
        else:
            subject = f"Your Membership in {community.name} Has Been Approved"
            body = (
                f"Hello,\n\n"
                f"Good news! Your reference has been accepted and your membership "
                f"in {community.name} is now fully approved.\n\n"
                f"You can now log in using your email address: {user.email}\n\n"
                f"Welcome aboard!\n"
                f"{community.name} Team"
            )

            svc.send_email(subject, body, to=user.email)
            logger.info(f"Approval email sent to '{user.email}'.")

    except Exception as e:
        logger.error(f"Failed to send approval email for reference '{ref_id}': {e}")

    return redirect("socialhub:profile_details", slug=request.user.community_profile.slug)



@require_POST
@login_required
def reference_reject(request, ref_id):
    ref = get_object_or_404(ReferenceRequest, pk=ref_id)

    # Only the referrer can reject
    if ref.referrer.user != request.user:
        raise PermissionDenied("You cannot modify this reference request.")

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

        svc = resolve_email_service(community)
        if svc is None:
            logger.info(f"No email service for '{community.name}'; skipping rejection email.")
        else:
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

            svc.send_email(subject, body, to=user.email)
            logger.info(f"Rejection email sent to '{user.email}'.")

    except Exception as e:
        logger.error(f"Failed to send rejection email for reference '{ref_id}': {e}")

    return redirect("socialhub:profile_details", slug=request.user.community_profile.slug)



