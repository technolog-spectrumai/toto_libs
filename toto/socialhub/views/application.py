from django.contrib.auth import get_user_model
from toto.core.models import Platform
from django.shortcuts import render, redirect, get_object_or_404
from toto.socialhub.models import Person, Community, MembershipApplication, generate_code, EmailService, ReferenceRequest
from toto.core.page import PageProcessor
from django.utils import timezone
from django.contrib.auth.models import User
from toto.socialhub.forms import MembershipApplicationForm, CodeVerificationForm, ReferenceRequestForm
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

        user, _ = User.objects.get_or_create(username=email, defaults={"email": email, "is_active": False})
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
            logger.info(f"New application created for '{email}' with code '{application.code}'.")
        else:
            logger.info(f"Existing application reused for '{email}'.")

        return redirect("socialhub:application_success", username=user.username)

    return render(request, "socialhub/membership_application.html", processor.decorate(context, request))


def application_success_view(request, username):
    processor = PageProcessor()
    context = {"page_title": "Application Submitted", "username": username}

    try:
        user = User.objects.get(username=username)
        application = MembershipApplication.objects.get(email=user.email)
        community = application.community
        svc = community.email_service or EmailService.objects.get(name="default-email-service")

        subject = "Your Membership Application"
        body = (
            f"Hello,\n\n"
            f"Thank you for applying to join {application.community.name}.\n"
            f"Your verification code is: {application.code}\n\n"
            f"Please enter this code on the verification page.\n\n"
            f"Best regards,\n"
            f"{application.community.name} Team"
        )

        svc.send_email(subject, body, to=user.email)

        logger.info(f"Confirmation email sent to '{user.email}'.")

    except Exception as e:
        # Email failure is NOT fatal for the page
        logger.error(f"Failed to send confirmation email for '{username}': {e}")

    # ---------------------------------------------------------
    # Render page
    # ---------------------------------------------------------
    logger.info(f"Application success page viewed for user '{username}'.")
    return render(request, "socialhub/application_success.html", processor.decorate(context, request))


def verify_application_view(request, username):
    processor = PageProcessor()
    form = CodeVerificationForm(request.POST or None)
    context = {"form": form, "page_title": "Verify Application", "username": username}

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
                logger.info(f"Verification successful for '{username}'.")
                return redirect("socialhub:reference_request", application_id=app.id)
        except MembershipApplication.DoesNotExist:
            context["error"] = "Invalid code or username."
            logger.warning(f"Verification failed: no application found for '{username}' with code '{code}'.")

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

        svc = community.email_service or EmailService.objects.get(name="default-email-service")

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

        svc = community.email_service or EmailService.objects.get(name="default-email-service")

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



