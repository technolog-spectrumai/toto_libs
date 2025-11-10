from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.views.generic import TemplateView
from django.utils import timezone
from django.contrib.auth.models import User
from community.models import MembershipApplication, generate_code, CommunityMember, Community
from community.forms import LoginForm, MembershipApplicationForm, CodeVerificationForm, ReferenceRequestForm
from oya.page import PageProcessor
import os
import logging

template_dir = "community"
logger = logging.getLogger(__name__)


def _get_template(name):
    return os.path.join(template_dir, name)


def _get_next(request):
    return request.GET.get('next') or 'nest:dashboard'


def membership_application_view(request):
    processor = PageProcessor()
    form = MembershipApplicationForm(request.POST or None)
    context = {"form": form, "page_title": "Apply for Membership"}

    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        community = form.cleaned_data["community"]

        user, _ = User.objects.get_or_create(username=email, defaults={"email": email})
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

        return redirect("community:application_success", username=user.username)

    return render(request, _get_template("membership_application.html"), processor.decorate(context, request))


def application_success_view(request, username):
    processor = PageProcessor()
    context = {"page_title": "Application Submitted", "username": username}
    logger.info(f"Application success page viewed for user '{username}'.")
    return render(request, _get_template("application_success.html"), processor.decorate(context, request))


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
                return redirect("community:reference_request", application_id=app.id)
        except MembershipApplication.DoesNotExist:
            context["error"] = "Invalid code or username."
            logger.warning(f"Verification failed: no application found for '{username}' with code '{code}'.")

    return render(request, _get_template("membership_verification.html"), processor.decorate(context, request))


def verification_success_view(request):
    processor = PageProcessor()
    context = {"page_title": "Verification Complete"}
    logger.info(f"Verification success page viewed by user '{request.user.username}'.")
    return render(request, _get_template("verification_success.html"), processor.decorate(context, request))


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
        return redirect("community:reference_next", application_id=application.id)

    return render(request, _get_template("reference_request.html"), processor.decorate(context, request))


def reference_next(request, application_id):
    processor = PageProcessor()
    application = get_object_or_404(MembershipApplication, pk=application_id)
    context = {
        "application": application,
        "page_title": "Thank You for Your Endorsement",
    }
    logger.info(f"Reference thank-you page viewed for application ID '{application_id}' by user '{request.user.username}'.")
    return render(request, _get_template("reference_next.html"), processor.decorate(context, request))


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
        context["companies"] = Community.objects.all()
        context["selected_company_id"] = self.request.GET.get("company")
        processor = PageProcessor()
        return processor.decorate(context, self.request)


def org_chart_data(request):
    company_slug = request.GET.get("company")
    if not company_slug:
        return JsonResponse({"nodes": []})

    try:
        company = Community.objects.get(slug=company_slug)
    except Community.DoesNotExist:
        logger.warning(f"Org chart data request failed: company slug '{company_slug}' not found.")
        return JsonResponse({"nodes": []})

    people = set()
    if company.head:
        people.add(company.head)
    for member in company.members.all():
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
        return "Head of Community"
    if person in company.members.all():
        return "Member"
    return "Contributor"
