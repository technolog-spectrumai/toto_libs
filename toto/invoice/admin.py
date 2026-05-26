import jinja2
from django.contrib import admin
from django.http import HttpResponse
from django.urls import path
from django.utils import timezone

from .models import BillingCycle, Invoice, InvoiceReport, InvoiceReportTemplate, InvoiceStatus, PaymentSettlement


class PaymentSettlementInline(admin.TabularInline):
    model = PaymentSettlement
    extra = 0
    readonly_fields = ("created_at",)


@admin.register(BillingCycle)
class BillingCycleAdmin(admin.ModelAdmin):
    list_display = ("name", "frequency", "active", "created_at")
    list_filter = ("frequency", "active")
    search_fields = ("name", "description")


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("pk", "issued_to", "title", "amount", "currency_label", "status", "bucket", "billing_cycle", "due_date", "paid_at", "created_at")
    list_filter = ("status", "currency_label")
    search_fields = ("issued_to__username", "title", "description", "notes", "obligation_reference")
    raw_id_fields = ("issued_to", "issued_by")
    readonly_fields = ("created_at", "updated_at", "paid_at")
    inlines = [PaymentSettlementInline]
    actions = ["mark_paid", "mark_cancelled"]

    @admin.action(description="Mark selected invoices as Paid")
    def mark_paid(self, request, queryset):
        now = timezone.now()
        updated = queryset.exclude(status="paid").update(status=InvoiceStatus.PAID, paid_at=now)
        self.message_user(request, f"{updated} invoice(s) marked as paid.")

    @admin.action(description="Mark selected invoices as Cancelled")
    def mark_cancelled(self, request, queryset):
        updated = queryset.exclude(status="cancelled").update(status=InvoiceStatus.CANCELLED)
        self.message_user(request, f"{updated} invoice(s) cancelled.")


@admin.register(PaymentSettlement)
class PaymentSettlementAdmin(admin.ModelAdmin):
    list_display = ("pk", "invoice", "amount", "currency_label", "method", "settled_at", "created_at")
    search_fields = ("reference", "notes", "invoice__title")
    readonly_fields = ("created_at",)


@admin.register(InvoiceReportTemplate)
class InvoiceReportTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "format", "is_active", "created_by", "created_at", "simulate_link")
    list_filter = ("format", "is_active")
    search_fields = ("name", "code", "description")
    readonly_fields = ("created_at", "updated_at")
    prepopulated_fields = {"code": ("name",)}

    def get_urls(self):
        urls = super().get_urls()
        extra = [
            path(
                "<int:template_pk>/simulate/",
                self.admin_site.admin_view(self.simulate_view),
                name="invoice_invoicereporttemplate_simulate",
            ),
        ]
        return extra + urls

    @admin.display(description="Simulate")
    def simulate_link(self, obj):
        from django.urls import reverse
        from django.utils.html import format_html
        url = reverse("admin:invoice_invoicereporttemplate_simulate", args=[obj.pk])
        return format_html('<a href="{}">▶ Simulate</a>', url)

    def simulate_view(self, request, template_pk):
        from django.shortcuts import get_object_or_404, render
        from toto.invoice.views import _MIME, _report_context

        tpl = get_object_or_404(InvoiceReportTemplate, pk=template_pk)

        invoice = Invoice.objects.select_related(
            "issued_to", "issued_by", "bucket", "bucket__tariff", "billing_cycle"
        ).order_by("-created_at").first()

        error = rendered = None
        if invoice:
            try:
                jenv = jinja2.Environment(autoescape=(tpl.format == "html"))
                rendered = jenv.from_string(tpl.template_source).render(**_report_context(invoice))
            except jinja2.TemplateError as exc:
                error = str(exc)

        if request.method == "POST" and rendered and not error:
            content_type = _MIME.get(tpl.format, "text/plain")
            resp = HttpResponse(rendered.encode(), content_type=content_type)
            resp["Content-Disposition"] = f'attachment; filename="simulate-{tpl.code}.{tpl.format}"'
            return resp

        context = {
            **self.admin_site.each_context(request),
            "template": tpl,
            "invoice": invoice,
            "rendered": rendered,
            "error": error,
            "opts": InvoiceReportTemplate._meta,
            "title": f"Simulate: {tpl.name}",
        }
        return render(request, "admin/invoice/simulate_report.html", context)


@admin.register(InvoiceReport)
class InvoiceReportAdmin(admin.ModelAdmin):
    list_display = ("pk", "invoice", "template", "generated_by", "vault_file", "generated_at")
    list_filter = ("template__format",)
    search_fields = ("invoice__title", "template__name")
    readonly_fields = ("generated_at", "rendered_output", "debug_context_data")

    @admin.display(description="Debug: raw invoice context data")
    def debug_context_data(self, obj):
        from django.utils.html import format_html
        from toto.invoice.views import _report_context

        invoice = Invoice.objects.select_related(
            "issued_to", "issued_by", "bucket", "bucket__tariff", "billing_cycle"
        ).get(pk=obj.invoice_id)

        ctx = _report_context(invoice)
        fmt = obj.template.format

        def _scalar(v):
            if hasattr(v, "isoformat"):
                return v.isoformat()
            if hasattr(v, "__dict__"):
                return str(v)
            return v

        flat = {k: _scalar(v) for k, v in ctx.items() if k != "settlements"}
        flat["settlements"] = [str(s) for s in ctx.get("settlements", [])]

        if fmt == "yaml":
            import yaml as _yaml
            raw = _yaml.dump(flat, allow_unicode=True, sort_keys=False, default_flow_style=False)
        elif fmt == "xml":
            lines = ["<context>"]
            for k, v in flat.items():
                if isinstance(v, list):
                    lines.append(f"  <{k}>")
                    for item in v:
                        lines.append(f"    <item>{item}</item>")
                    lines.append(f"  </{k}>")
                else:
                    lines.append(f"  <{k}>{v}</{k}>")
            lines.append("</context>")
            raw = "\n".join(lines)
        else:
            import json
            raw = json.dumps(flat, indent=2, default=str)

        return format_html('<pre>{}</pre>', raw)
