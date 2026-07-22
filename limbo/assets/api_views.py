import json
from decimal import Decimal

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from toto.api.cors import CorsApiView
from toto.assets.models import LedgerAccount, AssetHolding, LedgerEntry, from_base_units


@method_decorator(csrf_exempt, name="dispatch")
class WalletSummaryApiView(CorsApiView):
    def get(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)

        # ── Accounts + holdings ────────────────────────────────────────────────
        accounts_qs = (
            LedgerAccount.objects.filter(user=request.user, active=True)
            .prefetch_related("holdings__asset")
            .order_by("user_priority", "code")
        )
        accounts = []
        for acct in accounts_qs:
            holdings = []
            for h in acct.holdings.all():
                holdings.append(
                    {
                        "asset_name": h.asset.name,
                        "asset_unit": h.asset.unit_name,
                        "balance_base_units": h.balance_base_units,
                        "balance_display": str(h.balance_display),
                    }
                )
            accounts.append(
                {
                    "code": acct.code,
                    "name": acct.name,
                    "account_type": acct.account_type,
                    "holdings": holdings,
                }
            )

        # ── Pending usage charges ──────────────────────────────────────────────
        pending_charges = []
        try:
            from toto.tariffs.models import UsageRecord, UsageStatus

            records_qs = (
                UsageRecord.objects.filter(
                    payer_account__user=request.user,
                    status__in=[UsageStatus.PENDING, UsageStatus.RATED],
                )
                .select_related("tariff")
                .prefetch_related("charges__charged_asset")
                .order_by("-created_at")[:50]
            )
            for rec in records_qs:
                for charge in rec.charges.all():
                    pending_charges.append(
                        {
                            "metric_code": rec.metric_code,
                            "quantity": str(rec.quantity),
                            "unit": rec.unit,
                            "tariff_name": rec.tariff.name,
                            "asset_name": charge.charged_asset.name,
                            "asset_unit": charge.charged_asset.unit_name,
                            "amount_base_units": charge.amount_base_units,
                            "amount_display": str(
                                from_base_units(charge.amount_base_units, charge.charged_asset.decimals)
                            ),
                            "occurred_at": rec.occurred_at.isoformat() if rec.occurred_at else None,
                        }
                    )
        except Exception:
            pass

        # ── Open invoices ──────────────────────────────────────────────────────
        open_invoices = []
        try:
            from toto.invoice.models import Invoice, InvoiceStatus

            invoices_qs = Invoice.objects.filter(
                issued_to=request.user,
                status__in=[InvoiceStatus.PENDING, InvoiceStatus.OVERDUE],
            ).select_related("issued_by").order_by("due_date")[:50]

            for inv in invoices_qs:
                open_invoices.append(
                    {
                        "id": inv.id,
                        "title": inv.title,
                        "amount": str(inv.amount),
                        "currency": inv.currency_label,
                        "status": inv.status,
                        "due_date": inv.due_date.isoformat() if inv.due_date else None,
                        "issued_by_name": (
                            inv.issued_by.get_full_name() or inv.issued_by.username
                            if inv.issued_by
                            else None
                        ),
                    }
                )
        except Exception:
            pass

        # ── Totals ─────────────────────────────────────────────────────────────
        total_pending_by_asset: dict = {}
        for ch in pending_charges:
            key = ch["asset_unit"]
            total_pending_by_asset[key] = str(
                Decimal(total_pending_by_asset.get(key, "0")) + Decimal(ch["amount_display"])
            )

        total_open_invoices = str(
            sum(Decimal(inv["amount"]) for inv in open_invoices)
        )

        return JsonResponse(
            {
                "accounts": accounts,
                "pending_charges": pending_charges,
                "open_invoices": open_invoices,
                "totals": {
                    "total_pending_by_asset": total_pending_by_asset,
                    "total_open_invoice_amount": total_open_invoices,
                },
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class ObligationsApiView(CorsApiView):
    def get(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)

        from toto.assets.models import Obligation, ObligationStatus
        from django.utils import timezone

        account_pks = list(
            LedgerAccount.objects.filter(user=request.user, active=True).values_list("pk", flat=True)
        )
        obligations = (
            Obligation.objects
            .filter(debtor_account__in=account_pks, status=ObligationStatus.PENDING)
            .select_related("debtor_account", "creditor_account", "asset")
            .order_by("due_at")
        )
        result = []
        for ob in obligations:
            result.append({
                "id": ob.pk,
                "reference": ob.reference,
                "order_reference": ob.order_reference or "",
                "amount_display": str(ob.amount_display),
                "asset_unit": ob.asset.unit_name,
                "asset_name": ob.asset.name,
                "creditor_code": ob.creditor_account.code,
                "creditor_name": ob.creditor_account.name or ob.creditor_account.code,
                "debtor_code": ob.debtor_account.code,
                "due_at": ob.due_at.isoformat(),
                "is_overdue": ob.is_overdue,
                "status": ob.status,
            })
        return JsonResponse({"obligations": result})


@method_decorator(csrf_exempt, name="dispatch")
class MovementsApiView(CorsApiView):
    def get(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)

        entries = (
            LedgerEntry.objects
            .filter(account__user=request.user)
            .select_related("transaction", "asset", "account")
            .order_by("-created_at")[:30]
        )
        result = []
        for entry in entries:
            result.append({
                "transaction_reference": entry.transaction.reference,
                "amount_base_units": entry.amount_base_units,
                "amount_display": str(entry.amount_display),
                "asset_unit": entry.asset.unit_name,
                "account_code": entry.account.code,
                "created_at": entry.created_at.isoformat(),
                "is_credit": entry.amount_base_units >= 0,
            })
        return JsonResponse({"movements": result})


@method_decorator(csrf_exempt, name="dispatch")
class PinVerifyApiView(CorsApiView):
    def post(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)

        try:
            data = json.loads(request.body)
            raw_pin = data.get("pin", "")
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"ok": False, "error": "Invalid request."}, status=400)

        if not raw_pin:
            return JsonResponse({"ok": False, "error": "PIN is required."})

        from toto.bazaar.wallet_pin import check_wallet_pin, has_wallet_pin, mark_session_verified, issue_pin_token

        if not has_wallet_pin(request.user):
            return JsonResponse({"ok": False, "error": "No wallet PIN set. Set one via the portal first.", "no_pin": True})

        if check_wallet_pin(request.user, raw_pin):
            mark_session_verified(request.session)
            token = issue_pin_token(request.user)
            return JsonResponse({"ok": True, "pin_token": token})

        return JsonResponse({"ok": False, "error": "Incorrect PIN."})


@method_decorator(csrf_exempt, name="dispatch")
class ObligationFulfillApiView(CorsApiView):
    def post(self, request, pk):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)

        from toto.assets.models import Obligation, ObligationStatus
        from toto.assets.services.assets import fulfill_obligation
        from toto.bazaar.wallet_pin import has_wallet_pin, session_is_verified, verify_pin_token
        from django.core.exceptions import ValidationError

        try:
            body = json.loads(request.body) if request.body else {}
        except (json.JSONDecodeError, ValueError):
            body = {}

        try:
            obligation = (
                Obligation.objects
                .select_related("debtor_account", "creditor_account", "asset")
                .get(pk=pk, status=ObligationStatus.PENDING)
            )
        except Obligation.DoesNotExist:
            return JsonResponse({"error": "Obligation not found."}, status=404)

        if obligation.debtor_account.user_id != request.user.id:
            return JsonResponse({"error": "You can only fulfill your own obligations."}, status=403)

        if not has_wallet_pin(request.user):
            return JsonResponse({"error": "Set a wallet PIN first.", "no_pin": True}, status=400)

        pin_token = body.get("pin_token", "")
        token_ok = pin_token and verify_pin_token(pin_token, request.user)
        if not token_ok and not session_is_verified(request.session):
            return JsonResponse({"error": "PIN verification required.", "pin_required": True}, status=403)

        try:
            tx = fulfill_obligation(
                obligation=obligation,
                reference=f"fulfill-obligation-{obligation.pk}",
                description=f"Fulfilling obligation {obligation.reference}",
            )
            return JsonResponse({"ok": True, "transaction_reference": tx.reference})
        except ValidationError as exc:
            return JsonResponse({"error": "; ".join(exc.messages)}, status=400)
        except Exception as exc:
            return JsonResponse({"error": str(exc)}, status=400)
