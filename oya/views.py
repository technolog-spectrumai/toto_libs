from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from oya.models import DashboardBlock, MembershipApplication, generate_code, CommunityMember
from .page import PageProcessor
import os
from django.contrib.auth import authenticate, login
from django.contrib.auth import logout
from oya.forms import LoginForm, MembershipApplicationForm, CodeVerificationForm, ReferenceRequestForm
from django.utils import timezone
from django.contrib.auth.models import User
from django.shortcuts import render, get_object_or_404
from django.http import Http404

template_dir = "oya"

def _get_template(name):
    return os.path.join(template_dir, name)

def home_view(request):
    processor = PageProcessor()
    return render(request, _get_template("home.html"), processor.decorate({}, request))

def dashboard_view(request):
    processor = PageProcessor()
    dashboard_blocks = DashboardBlock.objects.all()

    context = {
        "page_title": "Dashboard",
        "blocks": dashboard_blocks
    }

    return render(request, _get_template("dashboard.html"), processor.decorate(context, request))

def login_view(request):
    processor = PageProcessor()
    form = LoginForm(request.POST or None)
    context = {"form": form, "page_title": "Login"}

    if request.method == "POST" and form.is_valid():
        user = authenticate(
            request,
            username=form.cleaned_data["username"],
            password=form.cleaned_data["password"]
        )
        if user:
            login(request, user)
            return redirect("dashboard")
        context["error"] = "Invalid credentials."

    return render(request, _get_template("login.html"), processor.decorate(context, request))

def logout_view(request):
    logout(request)
    return redirect("home")

def membership_application_view(request):
    processor = PageProcessor()
    form = MembershipApplicationForm(request.POST or None)
    context = {
        "form": form,
        "page_title": "Apply for Membership",
    }

    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        branch = form.cleaned_data["branch"]

        user, created = User.objects.get_or_create(
            username=email,
            defaults={"email": email}
        )

        application, created = MembershipApplication.objects.get_or_create(
            email=email,
            defaults={
                "branch": branch,
                "expires_at": timezone.now() + timezone.timedelta(days=7),
                "status": "pending"
            }
        )

        if created:
            # Only generate the code now if it's going to be emailed
            application.code = generate_code()
            application.save()

            # TODO: trigger email with code here

        return redirect("application_success", username=user.username)

    return render(request, _get_template("membership_application.html"), processor.decorate(context, request))


def application_success_view(request, username):
    processor = PageProcessor()
    context = {
        "page_title": "Application Submitted",
        "username": username
    }
    return render(request, _get_template("application_success.html"), processor.decorate(context, request))



def verify_application_view(request, username):
    processor = PageProcessor()
    form = CodeVerificationForm(request.POST or None)
    context = {
        "form": form,
        "page_title": "Verify Application",
        "username": username
    }

    if request.method == "POST" and form.is_valid():
        code = form.cleaned_data["code"]
        try:
            app = MembershipApplication.objects.get(code=code, email=username)
            if app.is_expired():
                context["error"] = "This code has expired."
            elif app.is_verified:
                context["message"] = "This application is already verified."
            else:
                app.verified_at = timezone.now()
                app.status = "verified"
                app.save()
                return redirect("reference_request", application_id=app.id)
        except MembershipApplication.DoesNotExist:
            context["error"] = "Invalid code or username. Please check and try again."

    return render(request, _get_template("membership_verification.html"), processor.decorate(context, request))


def verification_success_view(request):
    processor = PageProcessor()
    context = {
        "page_title": "Verification Complete"
    }
    return render(request, _get_template("verification_success.html"), processor.decorate(context, request))


def reference_request_view(request, application_id):
    processor = PageProcessor()

    try:
        application = MembershipApplication.objects.get(pk=application_id)
    except MembershipApplication.DoesNotExist:
        return redirect("application_not_found")

    form = ReferenceRequestForm(
        request.POST or None,
        application=application
    )

    context = {
        "form": form,
        "page_title": "Endorse Application",
        "application": application,
    }

    if request.method == "POST" and form.is_valid():
        reference_request = form.save(commit=False)
        reference_request.application = application
        reference_request.referrer = request.user.community_profile
        reference_request.save()

        # TODO: trigger notification to admins or log referral event here

        return redirect("reference_next", application_id=application.id)

    return render(
        request,
        _get_template("reference_request.html"),
        processor.decorate(context, request)
    )


def reference_next(request, application_id):
    processor = PageProcessor()
    application = get_object_or_404(MembershipApplication, pk=application_id)

    context = {
        "application": application,
        "page_title": "Thank You for Your Endorsement",
    }

    return render(
        request,
        _get_template("reference_next.html"),
        processor.decorate(context, request)
    )


@login_required
def profile_view(request):
    processor = PageProcessor()
    user = request.user

    try:
        profile = user.community_profile
    except CommunityMember.DoesNotExist:
        profile = None

    context = {
        "page_title": "Your Profile",
        "profile": profile,
        "username": user.username,
        "email": user.email,
    }

    return render(request, _get_template("profile.html"), processor.decorate(context, request))


@login_required
def not_implemented(request):
    processor = PageProcessor()
    context = {
        "page_title": "Not Implemented"
    }
    return render(request, _get_template("placeholder.html"), processor.decorate(context, request))


def root_view(request):
    try:
        processor = PageProcessor()
        index_url = processor.config.index_url
        if not index_url:
            raise Http404("No index URL configured for active platform.")
        return redirect(f"/{index_url}/")
    except Http404:
        raise
    except Exception:
        raise Http404("Failed to determine platform index URL.")





