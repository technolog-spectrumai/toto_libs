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
from django.views.generic import ListView
from .models import Post


template_dir = "community"


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
            return redirect(_get_next(request))
        context["error"] = "Invalid credentials."

    return render(request, _get_template("login.html"), processor.decorate(context, request))

def logout_view(request):
    logout(request)
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

            # TODO: trigger email with code here

        return redirect("community:application_success", username=user.username)

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


class PostListView(ListView):
    model = Post
    template_name = 'community/post_list.html'
    context_object_name = 'posts'
    ordering = ['-created_at']
    paginate_by = 10

    def get_queryset(self):
        queryset = Post.objects.filter(
            visibility='public'
        ).select_related('author')

        if 'member_slug' in self.kwargs:
            queryset = queryset.filter(author__slug=self.kwargs['member_slug'])

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['page_title'] = 'Posts'
        #context['current_member_slug'] = self.kwargs.get('member_slug')
        processor = PageProcessor()
        return processor.decorate(context, self.request)