import uuid as _uuid
from datetime import timedelta

import yaml
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import ListView

from toto.ui import PageProcessor

from .models import Invoice, InvoiceStatus


def _invoice_yaml(invoice) -> str:
    data = {
        "invoice": {
            "id": invoice.pk,
            "title": invoice.title,
            "description": invoice.description or None,
            "amount": float(invoice.amount),
            "currency": invoice.currency_label,
            "status": invoice.status,
            "bucket": invoice.bucket.slug if invoice.bucket else None,
            "billing_cycle": str(invoice.billing_cycle) if invoice.billing_cycle else None,
            "issued_to": invoice.issued_to.username,
            "issued_by": invoice.issued_by.username if invoice.issued_by else None,
            "created_at": invoice.created_at.strftime("%Y-%m-%d"),
            "due_date": str(invoice.due_date) if invoice.due_date else None,
            "paid_at": invoice.paid_at.strftime("%Y-%m-%d %H:%M") if invoice.paid_at else None,
            "notes": invoice.notes or None,
            "obligation_reference": invoice.obligation_reference or None,
        }
    }
    return yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)


class InvoiceListView(LoginRequiredMixin, ListView):
    template_name = "invoice/invoice_list.html"
    context_object_name = "invoices"
    paginate_by = 20

    def get_queryset(self):
        return Invoice.objects.filter(
            issued_to=self.request.user
        ).select_related("bucket", "billing_cycle", "issued_by").order_by("-created_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        qs = self.get_queryset()
        context["pending_count"] = qs.filter(status=InvoiceStatus.PENDING).count()
        context["paid_count"] = qs.filter(status=InvoiceStatus.PAID).count()
        context["invoice_rows"] = [
            {"invoice": inv, "yaml": _invoice_yaml(inv)}
            for inv in context["invoices"]
        ]
        return PageProcessor().decorate(context, self.request)


class AcceptInvoiceView(LoginRequiredMixin, View):
    """
    POST: creates an assets Obligation for the invoice amount.
    Only the invoice recipient may accept.
    """
    def post(self, request, pk):
        from toto.assets.models import Asset, LedgerAccount, Obligation

        invoice = get_object_or_404(Invoice, pk=pk, issued_to=request.user)

        if invoice.obligation_reference:
            messages.warning(request, "An obligation already exists for this invoice.")
            return redirect("invoice:invoice_list")

        try:
            asset = Asset.objects.get(unit_name=invoice.currency_label, active=True)
        except Asset.DoesNotExist:
            messages.error(request, f"Asset '{invoice.currency_label}' not found in ledger.")
            return redirect("invoice:invoice_list")

        debtor_account = LedgerAccount.objects.filter(user=request.user, active=True).first()
        if not debtor_account:
            messages.error(request, "No ledger account found for your user. Contact support.")
            return redirect("invoice:invoice_list")

        creditor_account = asset.reserve_account
        if not creditor_account:
            messages.error(request, f"No reserve account configured for '{invoice.currency_label}'.")
            return redirect("invoice:invoice_list")

        amount_base = int(invoice.amount * (10 ** asset.decimals))
        reference = f"INV-{invoice.pk}-{_uuid.uuid4().hex[:8].upper()}"
        due_at = timezone.now() + timedelta(days=30)

        Obligation.objects.create(
            reference=reference,
            debtor_account=debtor_account,
            creditor_account=creditor_account,
            asset=asset,
            amount_base_units=amount_base,
            due_at=due_at,
        )

        invoice.obligation_reference = reference
        invoice.save(update_fields=["obligation_reference", "updated_at"])

        messages.success(
            request,
            f"Obligation {reference} created — {invoice.amount} {invoice.currency_label} due in 30 days.",
        )
        return redirect("invoice:invoice_list")


class DownloadInvoiceYAMLView(LoginRequiredMixin, View):
    """GET: stream the invoice as a YAML file download."""
    def get(self, request, pk):
        invoice = get_object_or_404(Invoice, pk=pk, issued_to=request.user)
        content = _invoice_yaml(invoice).encode()
        response = HttpResponse(content, content_type="application/x-yaml")
        response["Content-Disposition"] = f'attachment; filename="invoice-{invoice.pk}.yaml"'
        return response


class InvoiceMetricsView(LoginRequiredMixin, View):
    template_name = "invoice/metrics.html"

    def get(self, request):
        qs = Invoice.objects.filter(issued_to=request.user)

        totals = qs.aggregate(
            total=Count("pk"),
            pending=Count("pk", filter=Q(status=InvoiceStatus.PENDING)),
            paid=Count("pk", filter=Q(status=InvoiceStatus.PAID)),
            overdue=Count("pk", filter=Q(status=InvoiceStatus.OVERDUE)),
            cancelled=Count("pk", filter=Q(status=InvoiceStatus.CANCELLED)),
        )

        by_currency = (
            qs.filter(status=InvoiceStatus.PAID)
            .values("currency_label")
            .annotate(total_paid=Sum("amount"))
            .order_by("currency_label")
        )

        pending_by_currency = (
            qs.filter(status=InvoiceStatus.PENDING)
            .values("currency_label")
            .annotate(total_pending=Sum("amount"))
            .order_by("currency_label")
        )

        recent = qs.select_related("bucket", "billing_cycle", "issued_by").order_by("-created_at")[:10]

        context = {
            "totals": totals,
            "paid_by_currency": list(by_currency),
            "pending_by_currency": list(pending_by_currency),
            "recent_invoices": recent,
        }
        return render(request, self.template_name, PageProcessor().decorate(context, request))
