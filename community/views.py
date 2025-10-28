from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from community.models import MembershipApplication, generate_code, CommunityMember
from .page import PageProcessor
import os
from django.contrib.auth import authenticate, login
from django.contrib.auth import logout
from community.forms import LoginForm, MembershipApplicationForm, CodeVerificationForm, ReferenceRequestForm
from django.utils import timezone
from django.contrib.auth.models import User
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.views.generic import TemplateView
from community.models import Company
import logging

template_dir = "community"



logger = logging.getLogger(__name__)

def _get_template(name):
    return os.path.join(template_dir, name)


def _get_next(request):
    next_url = request.GET.get('next')
    if next_url:
        return next_url
    return 'nest:dashboard'


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
            username = form.cleaned_data["username"]
            logger.info(f"User '{username}' logged in successfully.")
            return redirect(_get_next(request))
        else:
            username = form.cleaned_data["username"]
            logger.warning(f"Failed login attempt for username '{username}'.")
        context["error"] = "Invalid credentials."

    return render(request, _get_template("login.html"), processor.decorate(context, request))

def logout_view(request):
    logout(request)
    logger.info(f"User '{request.user.username}' logged out.")
    return redirect(_get_next(request))

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
            logger.info(f"New application created for '{email}' with code '{application.code}'.")
        else:
            logger.info(f"Existing application reused for '{email}'.")

            # TODO: trigger email with code here

        return redirect("community:application_success", username=user.username)

    return render(request, _get_template("membership_application.html"), processor.decorate(context, request))


def application_success_view(request, username):
    processor = PageProcessor()
    context = {
        "page_title": "Application Submitted",
        "username": username
    }
    logger.info(f"Application success page viewed for user '{username}'.")
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
                logger.warning(f"Verification failed: code expired for '{username}'.")
            elif app.is_verified:
                context["message"] = "This application is already verified."
                logger.info(f"Verification skipped: already verified for '{username}'.")
            else:
                app.verified_at = timezone.now()
                app.status = "verified"
                app.save()
                logger.info(f"Verification successful for '{username}'.")
                return redirect("reference_request", application_id=app.id)
        except MembershipApplication.DoesNotExist:
            context["error"] = "Invalid code or username. Please check and try again."
            logger.warning(f"Verification failed: no application found for '{username}' with code '{code}'.")

    return render(request, _get_template("membership_verification.html"), processor.decorate(context, request))


def verification_success_view(request):
    processor = PageProcessor()
    context = {
        "page_title": "Verification Complete"
    }
    logger.info(f"Verification success page viewed by user '{request.user.username}'.")
    return render(request, _get_template("verification_success.html"), processor.decorate(context, request))


def reference_request_view(request, application_id):
    processor = PageProcessor()
    logger.info(
        f"Reference request page accessed for application ID '{application_id}' by user '{request.user.username}'.")
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
        logger.info(f"Reference submitted by '{request.user.username}' for application ID '{application_id}'.")
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
    logger.info(
        f"Reference thank-you page viewed for application ID '{application_id}' by user '{request.user.username}'.")
    return render(
        request,
        _get_template("reference_next.html"),
        processor.decorate(context, request)
    )


@login_required
def profile_view(request, slug):
    processor = PageProcessor()
    member = get_object_or_404(CommunityMember, slug=slug)

    context = {
        "page_title": f"{member.display_name}'s Profile",
        "profile": member,
        "username": member.user.username,
        "email": member.user.email,
        "is_own_profile": member.user == request.user,
    }
    logger.info(f"Profile viewed: '{slug}' by user '{request.user.username}'.")
    return render(request, _get_template("profile.html"), processor.decorate(context, request))


class OrgChartView(TemplateView):
    template_name = "community/org_chart.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["companies"] = Company.objects.all()
        context["selected_company_id"] = self.request.GET.get("company")
        processor = PageProcessor()
        return processor.decorate(context, self.request)


def org_chart_data(request):
    company_slug = request.GET.get("company")
    if not company_slug:
        return JsonResponse({"nodes": []})

    try:
        company = Company.objects.get(slug=company_slug)
    except Company.DoesNotExist:
        logger.warning(f"Org chart data request failed: company slug '{company_slug}' not found.")
        return JsonResponse({"nodes": []})

    # Get all relevant people: head, branch heads, members
    people = set()

    if company.head:
        people.add(company.head)

    for branch in company.branches.all():
        if branch.head:
            people.add(branch.head)
        for member in branch.members.all():
            people.add(member)

    nodes = []
    for person in people:
        nodes.append({
            "id": f"person-{person.id}",
            "pid": f"person-{person.patron.id}" if person.patron else None,
            "name": person.display_name,
            "title": get_role(person, company),
            "img": person.avatar.url if person.avatar else None,
            "activity": person.slug,
            "profile": person.slug,
        })
    logger.info(f"Org chart data requested for company slug '{company_slug}' by user '{request.user.username}'.")
    return JsonResponse({"nodes": nodes})


def get_role(person, company):
    if person == company.head:
        return "Head of Company"
    for branch in company.branches.all():
        if person == branch.head:
            return f"Head of {branch.name}"
        if person in branch.members.all():
            return "Member"
    return "Contributor"