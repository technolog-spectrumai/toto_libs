from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import ListView, DetailView, TemplateView

from toto.core.page import PageProcessor

from .models import BountyBoard, BountyCategory, Bounty, BountyClaim, BountySubmission


class BountyContextMixin:
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return PageProcessor().decorate(context, self.request)


class BountyBoardListView(BountyContextMixin, ListView):
    model = BountyBoard
    template_name = 'bounty/board_list.html'
    context_object_name = 'boards'
    paginate_by = 20

    def get_queryset(self):
        return BountyBoard.objects.filter(is_active=True)


class BountyListView(BountyContextMixin, ListView):
    model = Bounty
    template_name = 'bounty/bounty_list.html'
    context_object_name = 'bounties'
    paginate_by = 24

    def get_queryset(self):
        board = get_object_or_404(BountyBoard, slug=self.kwargs['board_slug'])
        self._board = board
        qs = Bounty.objects.open().filter(board=board)
        q = self.request.GET.get('q')
        bounty_type = self.request.GET.get('type')
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(summary__icontains=q) | Q(description__icontains=q))
        if bounty_type:
            qs = qs.filter(bounty_type=bounty_type)
        sort = self.request.GET.get('sort', 'newest')
        if sort == 'reward_high':
            qs = qs.order_by('-reward_amount')
        elif sort == 'deadline':
            qs = qs.exclude(deadline__isnull=True).order_by('deadline')
        else:
            qs = qs.order_by('-created_at')
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['board'] = self._board
        context['categories'] = BountyCategory.objects.filter(board=self._board, is_active=True, parent__isnull=True)
        context['bounty_type_choices'] = Bounty.BOUNTY_TYPE_CHOICES
        return context


class BountyDetailView(BountyContextMixin, DetailView):
    model = Bounty
    template_name = 'bounty/bounty_detail.html'
    context_object_name = 'bounty'

    def get_object(self):
        return get_object_or_404(
            Bounty,
            board__slug=self.kwargs['board_slug'],
            slug=self.kwargs['slug'],
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        bounty = self.object
        context['board'] = bounty.board
        context['reviews'] = bounty.reviews.select_related('reviewer', 'hunter')[:10]
        user_claim = None
        if self.request.user.is_authenticated:
            user_claim = BountyClaim.objects.filter(bounty=bounty, user=self.request.user).first()
        context['user_claim'] = user_claim
        context['can_claim'] = (
            bounty.status == 'open'
            and bounty.slots_available > 0
            and user_claim is None
            and self.request.user.is_authenticated
        )
        return context


class BountyClaimCreateView(LoginRequiredMixin, View):
    def post(self, request, board_slug, slug):
        bounty = get_object_or_404(Bounty, board__slug=board_slug, slug=slug, status='open', is_public=True)
        if bounty.slots_available <= 0:
            messages.error(request, 'No claim slots available for this bounty.')
            return redirect('bounty:bounty-detail', board_slug=board_slug, slug=slug)
        if BountyClaim.objects.filter(bounty=bounty, user=request.user).exists():
            messages.warning(request, 'You have already claimed this bounty.')
            return redirect('bounty:bounty-detail', board_slug=board_slug, slug=slug)
        hunter = getattr(request.user, 'person', None)
        if not hunter:
            messages.error(request, 'You need a person profile to claim a bounty.')
            return redirect('bounty:bounty-detail', board_slug=board_slug, slug=slug)
        BountyClaim.objects.create(
            bounty=bounty,
            hunter=hunter,
            user=request.user,
            proposal=request.POST.get('proposal', '').strip(),
            status='pending',
        )
        messages.success(request, 'Your claim has been submitted and is awaiting approval.')
        return redirect('bounty:my-claims')


class BountyMyClaimsView(LoginRequiredMixin, BountyContextMixin, ListView):
    model = BountyClaim
    template_name = 'bounty/my_claims.html'
    context_object_name = 'claims'
    paginate_by = 20

    def get_queryset(self):
        return BountyClaim.objects.filter(user=self.request.user).select_related('bounty', 'bounty__board')


class BountySubmissionCreateView(LoginRequiredMixin, View):
    def get(self, request, claim_pk):
        claim = get_object_or_404(BountyClaim, pk=claim_pk, user=request.user, status__in=['accepted', 'working'])
        context = PageProcessor().decorate({'claim': claim}, request)
        return render(request, 'bounty/submission_form.html', context)

    def post(self, request, claim_pk):
        claim = get_object_or_404(BountyClaim, pk=claim_pk, user=request.user, status__in=['accepted', 'working'])
        body = request.POST.get('body', '').strip()
        if not body:
            messages.error(request, 'Submission body is required.')
            context = PageProcessor().decorate({'claim': claim}, request)
            return render(request, 'bounty/submission_form.html', context)
        BountySubmission.objects.create(
            claim=claim,
            title=request.POST.get('title', '').strip(),
            body=body,
            status='submitted',
        )
        claim.status = 'submitted'
        claim.save(update_fields=['status', 'updated_at'])
        bounty = claim.bounty
        if bounty.status == 'claimed':
            bounty.status = 'submitted'
            bounty.save(update_fields=['status', 'updated_at'])
        messages.success(request, 'Your work has been submitted for review.')
        return redirect('bounty:my-claims')


class BountyDashboardView(LoginRequiredMixin, BountyContextMixin, TemplateView):
    template_name = 'bounty/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        boards = BountyBoard.objects.filter(is_active=True)
        context['boards'] = boards
        context['pending_claims'] = BountyClaim.objects.filter(
            bounty__board__in=boards, status='pending',
        ).select_related('bounty', 'hunter')[:50]
        context['pending_submissions'] = BountySubmission.objects.filter(
            claim__bounty__board__in=boards, status='submitted',
        ).select_related('claim__bounty', 'claim__hunter')[:50]
        return context


class BountyClaimActionView(LoginRequiredMixin, View):
    def post(self, request, claim_pk):
        claim = get_object_or_404(BountyClaim, pk=claim_pk)
        action = request.POST.get('action')
        if action == 'accept' and claim.status == 'pending':
            claim.status = 'accepted'
            claim.accepted_at = timezone.now()
            claim.save(update_fields=['status', 'accepted_at', 'updated_at'])
            if claim.bounty.status == 'open':
                claim.bounty.status = 'claimed'
                claim.bounty.save(update_fields=['status', 'updated_at'])
            messages.success(request, f'Claim by {claim.hunter} accepted.')
        elif action == 'reject' and claim.status in ('pending', 'submitted'):
            claim.status = 'rejected'
            claim.save(update_fields=['status', 'updated_at'])
            messages.success(request, f'Claim by {claim.hunter} rejected.')
        else:
            messages.error(request, 'Invalid action.')
        return redirect('bounty:dashboard')


class BountySubmissionActionView(LoginRequiredMixin, View):
    def post(self, request, submission_pk):
        submission = get_object_or_404(BountySubmission, pk=submission_pk, status='submitted')
        action = request.POST.get('action')
        reviewer_note = request.POST.get('reviewer_note', '').strip()
        submission.reviewer_note = reviewer_note
        submission.reviewed_at = timezone.now()
        if action == 'approve':
            submission.status = 'approved'
            submission.save(update_fields=['status', 'reviewer_note', 'reviewed_at'])
            claim = submission.claim
            claim.status = 'approved'
            claim.completed_at = timezone.now()
            claim.save(update_fields=['status', 'completed_at', 'updated_at'])
            bounty = claim.bounty
            if all(c.status in ('approved', 'paid', 'rejected', 'cancelled') for c in bounty.claims.all()):
                bounty.status = 'completed'
                bounty.save(update_fields=['status', 'updated_at'])
            messages.success(request, 'Submission approved.')
        elif action == 'revision':
            submission.status = 'revision_requested'
            submission.save(update_fields=['status', 'reviewer_note', 'reviewed_at'])
            submission.claim.status = 'working'
            submission.claim.save(update_fields=['status', 'updated_at'])
            messages.success(request, 'Revision requested.')
        else:
            messages.error(request, 'Invalid action.')
        return redirect('bounty:dashboard')
