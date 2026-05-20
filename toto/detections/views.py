from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import ListView, DetailView, TemplateView

from toto.core.page import PageProcessor
from toto.assets.models import LedgerAccount

from .forms import DetectionMapCreateForm
from .models import (
    Detection, DetectionCategory, DetectionHandle,
    BountyBoard, Bounty, BountyClaim, BountySubmission, BountyPayment,
)
from .services import detection_map_feature, person_for_user, settle_bounty_payment


class DetectionsContextMixin:
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return PageProcessor().decorate(context, self.request)


# ---------------------------------------------------------------------------
# Detection views
# ---------------------------------------------------------------------------

class DetectionListView(DetectionsContextMixin, ListView):
    model = Detection
    template_name = 'detections/detection_list.html'
    context_object_name = 'detections'
    paginate_by = 30

    def get_queryset(self):
        qs = Detection.objects.select_related('category', 'address', 'zone', 'route', 'reported_by')
        q = self.request.GET.get('q')
        status = self.request.GET.get('status')
        severity = self.request.GET.get('severity')
        det_type = self.request.GET.get('type')
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q))
        if status:
            qs = qs.filter(status=status)
        if severity:
            qs = qs.filter(severity=severity)
        if det_type:
            qs = qs.filter(detection_type=det_type)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['categories'] = DetectionCategory.objects.filter(is_active=True, parent__isnull=True)
        context['severity_choices'] = Detection.SEVERITY_CHOICES
        context['type_choices'] = Detection.TYPE_CHOICES
        context['status_choices'] = Detection.STATUS_CHOICES
        features = [
            feature for feature in (
                detection_map_feature(detection)
                for detection in context['detections']
            )
            if feature
        ]
        context['detection_features'] = features
        return context


class DetectionDetailView(DetectionsContextMixin, DetailView):
    model = Detection
    template_name = 'detections/detection_detail.html'
    context_object_name = 'detection'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['handles'] = self.object.handles.select_related('assigned_to')
        context['bounties'] = Bounty.objects.filter(handle__detection=self.object)
        return context


class DetectionCreateView(LoginRequiredMixin, DetectionsContextMixin, View):
    template_name = 'detections/detection_form.html'

    def get(self, request):
        form = DetectionMapCreateForm(reporter=person_for_user(request.user))
        return self._render(request, form)

    def post(self, request):
        form = DetectionMapCreateForm(request.POST, reporter=person_for_user(request.user))
        if form.is_valid():
            detection = form.save()
            messages.success(request, 'Detection added from the map.')
            return redirect('detections:detection-detail', pk=detection.pk)
        messages.error(request, 'Check the detection details and map location.')
        return self._render(request, form)

    def _render(self, request, form):
        detections = Detection.objects.select_related('category', 'address', 'zone', 'route')[:100]
        features = [
            feature for feature in (
                detection_map_feature(detection)
                for detection in detections
            )
            if feature
        ]
        context = {
            'form': form,
            'detection_features': features,
        }
        return render(request, self.template_name, PageProcessor().decorate(context, request))


# ---------------------------------------------------------------------------
# Bounty board views
# ---------------------------------------------------------------------------

class BountyBoardListView(DetectionsContextMixin, ListView):
    model = BountyBoard
    template_name = 'detections/board_list.html'
    context_object_name = 'boards'
    paginate_by = 20

    def get_queryset(self):
        return BountyBoard.objects.filter(is_active=True)


class BountyListView(DetectionsContextMixin, ListView):
    model = Bounty
    template_name = 'detections/bounty_list.html'
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
        context['categories'] = DetectionCategory.objects.filter(is_active=True, parent__isnull=True)
        context['bounty_type_choices'] = Bounty.BOUNTY_TYPE_CHOICES
        return context


class BountyDetailView(DetectionsContextMixin, DetailView):
    model = Bounty
    template_name = 'detections/bounty_detail.html'
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
            return redirect('detections:bounty-detail', board_slug=board_slug, slug=slug)
        if BountyClaim.objects.filter(bounty=bounty, user=request.user).exists():
            messages.warning(request, 'You have already claimed this bounty.')
            return redirect('detections:bounty-detail', board_slug=board_slug, slug=slug)
        hunter = person_for_user(request.user)
        if not hunter:
            messages.error(request, 'You need a person profile to claim a bounty.')
            return redirect('detections:bounty-detail', board_slug=board_slug, slug=slug)
        BountyClaim.objects.create(
            bounty=bounty,
            hunter=hunter,
            user=request.user,
            proposal=request.POST.get('proposal', '').strip(),
            status='pending',
        )
        messages.success(request, 'Your claim has been submitted and is awaiting approval.')
        return redirect('detections:my-claims')


class BountyMyClaimsView(LoginRequiredMixin, DetectionsContextMixin, ListView):
    model = BountyClaim
    template_name = 'detections/my_claims.html'
    context_object_name = 'claims'
    paginate_by = 20

    def get_queryset(self):
        return BountyClaim.objects.filter(user=self.request.user).select_related('bounty', 'bounty__board')


class BountySubmissionCreateView(LoginRequiredMixin, View):
    def get(self, request, claim_pk):
        claim = get_object_or_404(BountyClaim, pk=claim_pk, user=request.user, status__in=['accepted', 'working'])
        context = PageProcessor().decorate({'claim': claim}, request)
        return render(request, 'detections/submission_form.html', context)

    def post(self, request, claim_pk):
        claim = get_object_or_404(BountyClaim, pk=claim_pk, user=request.user, status__in=['accepted', 'working'])
        body = request.POST.get('body', '').strip()
        if not body:
            messages.error(request, 'Submission body is required.')
            context = PageProcessor().decorate({'claim': claim}, request)
            return render(request, 'detections/submission_form.html', context)
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
        return redirect('detections:my-claims')


class BountyDashboardView(LoginRequiredMixin, DetectionsContextMixin, TemplateView):
    template_name = 'detections/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        boards = BountyBoard.objects.filter(is_active=True)
        context['boards'] = boards
        context['recent_detections'] = Detection.objects.filter(
            status__in=['new', 'acknowledged']
        ).select_related('category', 'reported_by')[:20]
        context['pending_claims'] = BountyClaim.objects.filter(
            bounty__board__in=boards, status='pending',
        ).select_related('bounty', 'hunter')[:50]
        context['pending_submissions'] = BountySubmission.objects.filter(
            claim__bounty__board__in=boards, status='submitted',
        ).select_related('claim__bounty', 'claim__hunter')[:50]
        context['pending_payments'] = BountyPayment.objects.filter(
            is_settled=False,
        ).select_related('claim__bounty', 'claim__hunter', 'receiver_ledger_account', 'asset', 'currency')[:20]
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
        return redirect('detections:dashboard')


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
            # Auto-create payment skeleton so the board operator can settle it
            BountyPayment.objects.get_or_create(
                claim=claim,
                defaults={
                    'amount': bounty.reward_amount,
                    'currency': bounty.reward_currency,
                    'asset': bounty.reward_asset or (bounty.reward_currency.asset if bounty.reward_currency_id else None),
                    'ledger_account': bounty.reward_ledger_account or bounty.board.ledger_account,
                    'receiver_ledger_account': (
                        LedgerAccount.objects
                        .filter(user=claim.user, active=True)
                        .order_by('pk')
                        .first()
                        if claim.user_id else None
                    ),
                    'note': f'Auto-created on approval of submission #{submission.pk}',
                    'is_settled': False,
                },
            )
            messages.success(request, 'Submission approved. Payment record created.')
        elif action == 'revision':
            submission.status = 'revision_requested'
            submission.save(update_fields=['status', 'reviewer_note', 'reviewed_at'])
            submission.claim.status = 'working'
            submission.claim.save(update_fields=['status', 'updated_at'])
            messages.success(request, 'Revision requested.')
        else:
            messages.error(request, 'Invalid action.')
        return redirect('detections:dashboard')


class BountyPaymentSettleView(LoginRequiredMixin, View):
    def post(self, request, payment_pk):
        payment = get_object_or_404(BountyPayment, pk=payment_pk, is_settled=False)
        ref = request.POST.get('ledger_tx_reference', '').strip()
        note = request.POST.get('note', '').strip()
        receiver_code = request.POST.get('receiver_ledger_account', '').strip()
        receiver_account = None
        if receiver_code:
            receiver_account = LedgerAccount.objects.filter(code=receiver_code, active=True).first()
            if not receiver_account:
                messages.error(request, f'No active ledger account found for code {receiver_code}.')
                return redirect('detections:dashboard')
        try:
            tx = settle_bounty_payment(
                payment=payment,
                receiver_account=receiver_account,
                reference=ref,
                note=note,
                paid_by=person_for_user(request.user),
            )
        except ValidationError as exc:
            messages.error(request, '; '.join(exc.messages) if hasattr(exc, 'messages') else str(exc))
            return redirect('detections:dashboard')
        messages.success(request, f'Payment settled for {payment.claim.hunter} via {tx.reference}.')
        return redirect('detections:dashboard')
