import uuid as _uuid
from datetime import timedelta

import jinja2
import yaml
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.base import ContentFile
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import ListView

from toto.ui import PageProcessor

from .models import Invoice, InvoiceReport, InvoiceReportTemplate, InvoiceStatus


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
        context["report_templates"] = list(
            InvoiceReportTemplate.objects.filter(is_active=True).order_by("name")
        )

        import json
        from toto.vault.models import Bucket, VaultDirectory
        user_buckets = list(Bucket.objects.filter(owner=self.request.user).order_by("name"))
        context["user_buckets"] = user_buckets
        context["bucket_directories_json"] = json.dumps({
            str(b.pk): [
                {"pk": d.pk, "name": d.full_path()}
                for d in VaultDirectory.objects.filter(bucket=b).order_by("name")
            ]
            for b in user_buckets
        })
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

        if invoice.amount <= 0:
            messages.error(request, "Cannot create an obligation for a zero or negative invoice.")
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


def _report_context(invoice):
    """Build the Jinja2 rendering context for an invoice."""
    from django.utils import timezone as tz
    return {
        "invoice": invoice,
        "issued_to": invoice.issued_to,
        "issued_by": invoice.issued_by,
        "bucket": invoice.bucket,
        "tariff": invoice.bucket.tariff if invoice.bucket_id and invoice.bucket else None,
        "billing_cycle": invoice.billing_cycle,
        "settlements": list(invoice.settlements.all()),
        "now": tz.now(),
    }


_MIME = {"html": "text/html", "xml": "application/xml", "yaml": "application/x-yaml"}


class GenerateReportView(LoginRequiredMixin, View):
    """POST: render template → save InvoiceReport to DB → stream file download in one step."""

    def post(self, request, pk):
        invoice = get_object_or_404(Invoice, pk=pk, issued_to=request.user)

        template_id = request.POST.get("template_id")
        bucket_id = request.POST.get("bucket_id") or None
        directory_id = request.POST.get("directory_id") or None

        template = get_object_or_404(InvoiceReportTemplate, pk=template_id, is_active=True)

        try:
            jenv = jinja2.Environment(
                autoescape=(template.format == "html"),
                undefined=jinja2.Undefined,
            )
            rendered = jenv.from_string(template.template_source).render(
                **_report_context(invoice)
            )
        except jinja2.TemplateError as exc:
            messages.error(request, f"Template error: {exc}")
            return redirect("invoice:invoice_list")

        vault_file = None
        if bucket_id:
            from toto.vault.models import Bucket, VaultDirectory, VaultFile
            try:
                bucket = Bucket.objects.get(pk=bucket_id, owner=request.user)
            except Bucket.DoesNotExist:
                messages.error(request, "Bucket not found.")
                return redirect("invoice:invoice_list")

            directory = None
            if directory_id:
                directory = VaultDirectory.objects.filter(pk=directory_id, bucket=bucket).first()

            filename = f"invoice-{invoice.pk}-report.{template.format}"
            cf = ContentFile(rendered.encode(), name=filename)
            vault_file = VaultFile(
                owner=request.user,
                title=f"Invoice #{invoice.pk} — {template.name}",
                file_type=template.format,
                bucket=bucket,
                directory=directory,
                is_public=False,
            )
            vault_file.file.save(filename, cf, save=False)
            vault_file.save()

        report = InvoiceReport.objects.create(
            invoice=invoice,
            template=template,
            generated_by=request.user,
            rendered_output=rendered,
            vault_file=vault_file,
        )

        content_type = _MIME.get(template.format, "text/plain")
        response = HttpResponse(rendered.encode(), content_type=content_type)
        response["Content-Disposition"] = f'attachment; filename="{report.filename()}"'
        return response


class DownloadReportView(LoginRequiredMixin, View):
    """GET: re-download a previously generated InvoiceReport."""

    def get(self, request, pk):
        report = get_object_or_404(InvoiceReport, pk=pk, invoice__issued_to=request.user)
        content_type = _MIME.get(report.template.format, "text/plain")
        response = HttpResponse(report.rendered_output.encode(), content_type=content_type)
        response["Content-Disposition"] = f'attachment; filename="{report.filename()}"'
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
