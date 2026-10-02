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
from django.utils.crypto import constant_time_compare
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _


#: The log names an application by its id (2026-10-01, 37c.21): never the
#: address, the username or the code, which the container's log kept and
#: shipped wherever its lines went.
logger = logging.getLogger(__name__)
User = get_user_model()

# The purpose tag toto.jess reads off the message and pops. On a host without jess the
# header is simply carried through to SMTP, so this costs nothing there.
_JESS_PURPOSE_HEADER = "X-Jess-Purpose"

#: The application this browser applied with (2026-10-01, 37c.21): its id and
#: the end of its week (``expires_at``, which a renewal moves on). The
#: verification page, the reference step and its thank-you page find their
#: application through it, so no URL carries the address typed or the
#: application's number — the pages used to be /apply/success/<e-mail>/,
#: /verify/user/<e-mail>/ and /reference/submit/<number counted up from 1>/,
#: and paths go into nginx's log and other sites' Referer headers — and the
#: code's picture is shown only to the browser that applied.
APPLIED_SESSION_KEY = "socialhub_application"

#: The applications this browser verified, by id, each with the moment it was
#: verified (2026-10-01, the review of stage 35). The reference step — the
#: referrer, the message and the password — opens only for the one it applied
#: with and verified: before, anybody holding an application's id could set
#: the password of a pending applicant's account and read their address.
VERIFIED_SESSION_KEY = "socialhub_verified_applications"


def remember_applied(session, application):
    """Put ``application`` in this browser's session — the application view,
    and tests that make an application without the form."""
    session[APPLIED_SESSION_KEY] = {"id": application.pk,
                                    "round": application.expires_at.isoformat()}


def _applied_here(request):
    """The application this browser applied with, in the same round; else None.

    A renewal (``applications.renew``) gives the application a new week, so a
    browser that applied in an earlier round — maybe not the applicant's — no
    longer reaches its code once the address applied again.
    """
    held = request.session.get(APPLIED_SESSION_KEY)
    if not isinstance(held, dict) or not isinstance(held.get("round"), str):
        return None
    when = parse_datetime(held["round"])
    application = (MembershipApplication.objects.select_related("community")
                   .filter(pk=held.get("id")).first() if when is not None else None)
    if application is None or application.expires_at != when:
        return None
    return application


def _remember_verified(request, application):
    verified = dict(request.session.get(VERIFIED_SESSION_KEY) or {})
    verified[str(application.pk)] = application.verified_at.isoformat()
    request.session[VERIFIED_SESSION_KEY] = verified


def _verified_here(request):
    """The application this browser applied with, when it also verified it
    here; else None.

    The moment must still be the application's own: a renewal clears
    ``verified_at``, so the browser that verified an earlier round no longer
    counts once the address applied again.
    """
    application = _applied_here(request)
    if application is None:
        return None
    stamp = (request.session.get(VERIFIED_SESSION_KEY) or {}).get(str(application.pk))
    when = parse_datetime(stamp) if isinstance(stamp, str) else None
    return application if when is not None and application.verified_at == when else None


def _not_this_browser(request, processor):
    """The reference step's refusal: a sentence, no form, no address, nothing set."""
    logger.warning("Reference step refused: no application verified in this browser.")
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
               "privacy_notice": form.privacy_notice,
               # An application this browser made and has not verified yet:
               # the page offers the way back to its code.
               "applied": _applied_here(request) is not None}

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
            remember_applied(request.session, application)
            logger.info("Lapsed application %s renewed.", application.pk)
            return redirect("socialhub:application_success")

        # The login username is chosen by the applicant (validated unique in the
        # form); the email is the application's key — one application per
        # address. The account stays inactive until a reference is accepted.
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
            # This browser's from now on (2026-10-01, 37c.21): the next pages
            # find it in the session, never in their address.
            remember_applied(request.session, application)
            logger.info("Application %s created.", application.pk)
        else:
            # Only a race reaches here (the form refuses a taken address):
            # the browser that made the application keeps it.
            logger.info("Application %s reused.", application.pk)

        return redirect("socialhub:application_success")

    return render(request, "socialhub/membership_application.html", processor.decorate(context, request))


def application_success_view(request):
    processor = PageProcessor()
    # The verification code is never emailed: the verification page shows it
    # as a CAPTCHA the applicant retypes, and the reference/endorsement step
    # is what actually gates membership. Nothing names the applicant here
    # (2026-10-01, 37c.21): the application is the session's.
    context = {"page_title": "Application Submitted",
               "applied": _applied_here(request) is not None}

    logger.info("Application success page viewed.")
    return render(request, "socialhub/application_success.html", processor.decorate(context, request))


def verify_application_view(request):
    processor = PageProcessor()
    form = CodeVerificationForm(request.POST or None)
    context = {"form": form, "page_title": "Verify Application"}

    # The code is never mailed — it is shown as a distorted CAPTCHA the
    # applicant retypes to prove they are human. The reference/endorsement
    # step is what actually gates membership. Only to the browser that applied
    # (2026-10-01, 37c.21): the page used to answer anybody who typed an
    # address into its URL, with that application's code.
    application = _applied_here(request)
    if application is None:
        context["refusal"] = _("This browser has no application waiting for its code. "
                               "Apply for membership first; if you applied in another "
                               "browser, type the code there.")
        return render(request, "socialhub/membership_verification.html",
                      processor.decorate(context, request), status=403)
    context["application"] = application
    try:
        context["captcha_image"] = generate_code_captcha(application.code)
    except Exception as e:
        logger.error("Failed to render the CAPTCHA for application %s: %s",
                     application.pk, type(e).__name__)

    # Rate-limit retries the same way login does: a failed code starts a
    # short cooldown during which the form is disabled.
    if request.method == "POST":
        remaining = captcha_retry_cooldown_remaining(request)
        if remaining > 0:
            context["error"] = f"Please wait {remaining} seconds before trying again."
            context["cooldown_remaining"] = remaining
            logger.warning("CAPTCHA retry blocked by the cooldown for application %s (%ss left).",
                           application.pk, remaining)
            return render(request, "socialhub/membership_verification.html", processor.decorate(context, request))

    if request.method == "POST" and form.is_valid():
        app = application
        if not constant_time_compare(form.cleaned_data["code"].strip(), app.code):
            context["error"] = _("That is not the code in the picture.")
            logger.warning("Verification failed: a wrong code for application %s.", app.pk)
            context["cooldown_remaining"] = captcha_retry_cooldown_seconds()
            start_captcha_retry_cooldown(request)
        elif app.is_expired():
            # Since 2026-10-01 applying again renews a lapsed application
            # (applications.renew), so the answer says how.
            context["error"] = _("This code has expired. Apply again with the same "
                                 "e-mail address to get a new one.")
            logger.warning("Verification failed: application %s has expired.", app.pk)
        elif app.is_verified:
            context["message"] = "This application is already verified."
            logger.info("Verification skipped: application %s is already verified.", app.pk)
        else:
            app.verified_at = timezone.now()
            app.status = "verified"
            app.save()
            # The reference step is this browser's from now on (2026-10-01).
            _remember_verified(request, app)
            clear_captcha_retry_cooldown(request)
            logger.info("Application %s verified.", app.pk)
            return redirect("socialhub:reference_request")

    return render(request,"socialhub/membership_verification.html", processor.decorate(context, request))


def verification_success_view(request):
    processor = PageProcessor()
    context = {"page_title": "Verification Complete"}
    logger.info("Verification success page viewed.")
    return render(request, "socialhub/verification_success.html", processor.decorate(context, request))


def reference_request_view(request):
    processor = PageProcessor()
    # Only the browser that applied and verified the code (2026-10-01): the
    # page sets the account's password. Its address names no application.
    application = _verified_here(request)
    if application is None:
        return _not_this_browser(request, processor)
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
        logger.info("Reference requested for application %s.", application.pk)

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
                logger.info("The applicant of application %s set a password at the "
                            "reference step.", application.pk)

        return redirect("socialhub:reference_next")

    return render(request, "socialhub/reference_request.html", processor.decorate(context, request))


def reference_next(request):
    processor = PageProcessor()
    # The verifying browser's too (2026-10-01).
    application = _verified_here(request)
    if application is None:
        return _not_this_browser(request, processor)
    context = {
        "application": application,
        "page_title": "Thank You for Your Endorsement",
    }
    logger.info("Reference thank-you page viewed for application %s.", application.pk)
    return render(request, "socialhub/reference_next.html", processor.decorate(context, request))


@require_POST
@login_required
def reference_accept(request, ref_id):
    ref = get_object_or_404(ReferenceRequest, pk=ref_id)

    # Only the referrer can accept
    if ref.referrer.user != request.user:
        raise PermissionDenied(_("You cannot modify this reference request."))

    # The account this acceptance lets in (2026-10-02): the one
    # ReferenceRequest.save activates, found while it still waits — after, it
    # is active like a member's with the same address, and the address alone
    # found two accounts and sent no mail.
    applicant = applications.applicant_account(ref.application, waiting_only=True)
    ref.status = "accepted"
    ref.responded_at = timezone.now()
    ref.save()  # triggers your model logic to activate the user

    # ---------------------------------------------------------
    # Send email to the applicant (they can now log in)
    # ---------------------------------------------------------
    try:
        application = ref.application
        user = applicant or applications.applicant_account(application)
        community = application.community

        # Sign-in is by username (2026-10-02): the mail said "using your
        # email address", which signs nobody in — an address typed where the
        # username goes is tried at whichever account has it as its username.
        subject = f"Your Membership in {community.name} Has Been Approved"
        body = (
            f"Hello,\n\n"
            f"Good news! Your reference has been accepted and your membership "
            f"in {community.name} is now fully approved.\n\n"
            f"You can now sign in with your username: {user.get_username()}\n\n"
            f"Welcome aboard!\n"
            f"{community.name} Team"
        )
        _send_endorsement_mail(subject, body, to=user.email, community=community)
        logger.info("Approval mail queued for application %s.", application.pk)

    except Exception as e:
        logger.error("Failed to send the approval mail for reference %s: %s", ref_id,
                     type(e).__name__)

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
        logger.info("Rejection mail queued for application %s.", application.pk)

    except Exception as e:
        logger.error("Failed to send the rejection mail for reference %s: %s", ref_id,
                     type(e).__name__)

    return redirect("socialhub:profile_details", slug=request.user.community_profile.slug)



