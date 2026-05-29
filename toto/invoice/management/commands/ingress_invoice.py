from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import CommandError

from toto.ingress import IngressCommand
from toto.invoice.models import (
    BillingCycle,
    BillingCycleFrequency,
    Invoice,
    InvoiceReportTemplate,
    InvoiceStatus,
    ReportFormat,
)

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Invoice #{{ invoice.pk }}</title>
  <style>
    body { font-family: sans-serif; max-width: 800px; margin: 40px auto; color: #222; }
    h1 { border-bottom: 2px solid #333; padding-bottom: 8px; }
    table { width: 100%; border-collapse: collapse; margin-top: 20px; }
    th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid #ddd; }
    th { background: #f5f5f5; }
    .amount { font-size: 1.4em; font-weight: bold; }
    .meta { color: #666; font-size: .9em; }
    .status-pending { color: #c97a00; }
    .status-paid    { color: #2a7a2a; }
    .status-overdue { color: #c00; }
    .status-cancelled { color: #888; }
  </style>
</head>
<body>
  <h1>Invoice #{{ invoice.pk }}</h1>

  <p class="amount">{{ invoice.amount }} {{ invoice.currency_label }}</p>
  <p class="status-{{ invoice.status }}"><strong>Status:</strong> {{ invoice.status | upper }}</p>

  <table>
    <tr><th>Title</th><td>{{ invoice.title }}</td></tr>
    {% if invoice.description %}<tr><th>Description</th><td>{{ invoice.description }}</td></tr>{% endif %}
    <tr><th>Issued to</th><td>{{ issued_to.get_full_name() or issued_to.username }} ({{ issued_to.email }})</td></tr>
    {% if issued_by %}<tr><th>Issued by</th><td>{{ issued_by.get_full_name() or issued_by.username }}</td></tr>{% endif %}
    <tr><th>Created</th><td>{{ invoice.created_at.strftime('%Y-%m-%d') }}</td></tr>
    {% if invoice.due_date %}<tr><th>Due date</th><td>{{ invoice.due_date }}</td></tr>{% endif %}
    {% if invoice.paid_at %}<tr><th>Paid at</th><td>{{ invoice.paid_at.strftime('%Y-%m-%d %H:%M') }}</td></tr>{% endif %}
    {% if bucket %}<tr><th>Bucket</th><td>{{ bucket.name }} ({{ bucket.slug }})</td></tr>{% endif %}
    {% if tariff %}<tr><th>Tariff</th><td>{{ tariff.code }} — {{ tariff.name }}</td></tr>{% endif %}
    {% if billing_cycle %}<tr><th>Billing cycle</th><td>{{ billing_cycle.name }}</td></tr>{% endif %}
    {% if invoice.obligation_reference %}<tr><th>Obligation ref</th><td>{{ invoice.obligation_reference }}</td></tr>{% endif %}
    {% if invoice.notes %}<tr><th>Notes</th><td>{{ invoice.notes }}</td></tr>{% endif %}
  </table>

  {% if settlements %}
  <h2>Settlements</h2>
  <table>
    <tr><th>Date</th><th>Amount</th><th>Method</th><th>Reference</th></tr>
    {% for s in settlements %}
    <tr>
      <td>{{ s.settled_at.strftime('%Y-%m-%d') }}</td>
      <td>{{ s.amount }} {{ s.currency_label }}</td>
      <td>{{ s.method or "—" }}</td>
      <td class="meta">{{ s.reference or "—" }}</td>
    </tr>
    {% endfor %}
  </table>
  {% endif %}

  <p class="meta" style="margin-top:40px">Generated: {{ now.strftime('%Y-%m-%d %H:%M') }}</p>
</body>
</html>
"""

YAML_TEMPLATE = """\
invoice:
  id: {{ invoice.pk }}
  title: {{ invoice.title | string | tojson }}
  description: {{ invoice.description | tojson if invoice.description else 'null' }}
  amount: {{ invoice.amount }}
  currency: {{ invoice.currency_label | tojson }}
  status: {{ invoice.status | tojson }}
  issued_to:
    username: {{ issued_to.username | tojson }}
    email: {{ issued_to.email | tojson }}
    name: {{ (issued_to.get_full_name() or issued_to.username) | tojson }}
  issued_by: {{ issued_by.username | tojson if issued_by else 'null' }}
  created_at: {{ invoice.created_at.strftime('%Y-%m-%d') | tojson }}
  due_date: {{ invoice.due_date | string | tojson if invoice.due_date else 'null' }}
  paid_at: {{ invoice.paid_at.strftime('%Y-%m-%d %H:%M') | tojson if invoice.paid_at else 'null' }}
  bucket: {{ bucket.slug | tojson if bucket else 'null' }}
  tariff: {{ tariff.code | tojson if tariff else 'null' }}
  billing_cycle: {{ billing_cycle.name | tojson if billing_cycle else 'null' }}
  obligation_reference: {{ invoice.obligation_reference | tojson if invoice.obligation_reference else 'null' }}
  notes: {{ invoice.notes | tojson if invoice.notes else 'null' }}
{% if settlements %}
  settlements:
{% for s in settlements %}
    - date: {{ s.settled_at.strftime('%Y-%m-%d') | tojson }}
      amount: {{ s.amount }}
      currency: {{ s.currency_label | tojson }}
      method: {{ s.method | tojson if s.method else 'null' }}
      reference: {{ s.reference | tojson if s.reference else 'null' }}
{% endfor %}
{% endif %}
generated_at: {{ now.strftime('%Y-%m-%d %H:%M') | tojson }}
"""

XML_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<invoice id="{{ invoice.pk }}">
  <title>{{ invoice.title }}</title>
  <description>{{ invoice.description or '' }}</description>
  <amount currency="{{ invoice.currency_label }}">{{ invoice.amount }}</amount>
  <status>{{ invoice.status }}</status>
  <issued_to>
    <username>{{ issued_to.username }}</username>
    <email>{{ issued_to.email }}</email>
    <name>{{ issued_to.get_full_name() or issued_to.username }}</name>
  </issued_to>
  <issued_by>{{ issued_by.username if issued_by else '' }}</issued_by>
  <created_at>{{ invoice.created_at.strftime('%Y-%m-%d') }}</created_at>
  <due_date>{{ invoice.due_date or '' }}</due_date>
  <paid_at>{{ invoice.paid_at.strftime('%Y-%m-%d %H:%M') if invoice.paid_at else '' }}</paid_at>
  <bucket>{{ bucket.slug if bucket else '' }}</bucket>
  <tariff>{{ tariff.code if tariff else '' }}</tariff>
  <billing_cycle>{{ billing_cycle.name if billing_cycle else '' }}</billing_cycle>
  <obligation_reference>{{ invoice.obligation_reference or '' }}</obligation_reference>
  <notes>{{ invoice.notes or '' }}</notes>
  {% if settlements %}
  <settlements>
    {% for s in settlements %}
    <settlement>
      <date>{{ s.settled_at.strftime('%Y-%m-%d') }}</date>
      <amount currency="{{ s.currency_label }}">{{ s.amount }}</amount>
      <method>{{ s.method or '' }}</method>
      <reference>{{ s.reference or '' }}</reference>
    </settlement>
    {% endfor %}
  </settlements>
  {% endif %}
  <generated_at>{{ now.strftime('%Y-%m-%d %H:%M') }}</generated_at>
</invoice>
"""


class Command(IngressCommand):
    help = "Seed invoice app: billing cycles, report templates, demo invoices (--full)."

    def process(self):
        # ── Billing cycles ─────────────────────────────────────────────────────
        cycles = [
            ("Monthly",   BillingCycleFrequency.MONTHLY,  "Invoice once per calendar month."),
            ("Quarterly", BillingCycleFrequency.MONTHLY,  "Invoice once per quarter (~3 months)."),
            ("Yearly",    BillingCycleFrequency.YEARLY,   "Annual invoice."),
        ]
        for name, freq, desc in cycles:
            _, created = BillingCycle.objects.get_or_create(
                name=name,
                defaults={"frequency": freq, "description": desc, "active": True},
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f"  + billing cycle: {name}"))
        self.stdout.write("Billing cycles ready.")

        # ── Report templates ───────────────────────────────────────────────────
        templates = [
            (
                "Standard HTML Invoice",
                "standard-html",
                ReportFormat.HTML,
                "Full invoice details rendered as a printable HTML document.",
                HTML_TEMPLATE,
            ),
            (
                "YAML Invoice Export",
                "yaml-export",
                ReportFormat.YAML,
                "Machine-readable YAML snapshot of the invoice — suitable for archiving or API ingestion.",
                YAML_TEMPLATE,
            ),
            (
                "XML Invoice Export",
                "xml-export",
                ReportFormat.XML,
                "Structured XML representation of the invoice for legal or accounting systems.",
                XML_TEMPLATE,
            ),
        ]

        for name, code, fmt, desc, source in templates:
            tpl, created = InvoiceReportTemplate.objects.get_or_create(
                code=code,
                defaults={
                    "name": name,
                    "format": fmt,
                    "description": desc,
                    "template_source": source,
                    "is_active": True,
                },
            )
            if not created and tpl.template_source != source:
                tpl.template_source = source
                tpl.name = name
                tpl.description = desc
                tpl.save(update_fields=["name", "description", "template_source", "updated_at"])
                self.stdout.write(self.style.WARNING(f"  ~ updated template: {code}"))
            elif created:
                self.stdout.write(self.style.SUCCESS(f"  + report template: {code} [{fmt}]"))
        self.stdout.write("Report templates ready.")

        if not self.full:
            self.stdout.write(self.style.SUCCESS("✅  Invoice ingress complete (use --full for demo invoices)."))
            return

        # ── Demo invoices (--full only) ────────────────────────────────────────
        try:
            user = User.objects.get(username="admin")
        except User.DoesNotExist:
            raise CommandError("Demo user 'admin' not found. Run ingress_core first.")

        cycle_monthly = BillingCycle.objects.get(name="Monthly")

        demo_invoices = [
            {
                "title": "Demo Storage Invoice — January 2026",
                "description": "Monthly storage billing demo invoice.",
                "amount": Decimal("12.50"),
                "currency_label": "TUSD",
                "status": InvoiceStatus.PAID,
            },
            {
                "title": "Demo Storage Invoice — February 2026",
                "description": "Monthly storage billing demo invoice.",
                "amount": Decimal("14.00"),
                "currency_label": "TUSD",
                "status": InvoiceStatus.PENDING,
            },
            {
                "title": "Demo Service Invoice — Q1 2026",
                "description": "Quarterly service billing demo invoice.",
                "amount": Decimal("250.00"),
                "currency_label": "TUSD",
                "status": InvoiceStatus.OVERDUE,
            },
        ]

        created_count = 0
        for data in demo_invoices:
            if Invoice.objects.filter(issued_to=user, title=data["title"]).exists():
                continue
            Invoice.objects.create(
                issued_to=user,
                issued_by=user,
                billing_cycle=cycle_monthly,
                **data,
            )
            created_count += 1
            self.stdout.write(self.style.SUCCESS(f"  + invoice: {data['title']}"))

        if created_count == 0:
            self.stdout.write("Demo invoices already exist — skipped.")

        self.stdout.write(self.style.SUCCESS("✅  Invoice ingress complete."))
