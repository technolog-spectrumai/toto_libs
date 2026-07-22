import json
from decimal import Decimal, InvalidOperation

from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from toto.api.cors import CorsApiView
from toto.assets.models import Asset, LedgerAccount, to_base_units

from .models import AssetExchangeRequest, ExchangeRequestStatus


def _ser_request(req, user_id):
    return {
        "id": req.pk,
        "direction": "incoming" if req.counterparty_id == user_id else "outgoing",
        "status": req.status,
        "requester": req.requester.username if req.requester else "",
        "counterparty": req.counterparty.username if req.counterparty else None,
        "requester_account": req.requester_account.code if req.requester_account else "",
        "offer_asset": req.offer_asset.unit_name,
        "offer_amount": str(req.offer_amount_display),
        "request_asset": req.request_asset.unit_name,
        "request_amount": str(req.request_amount_display),
        "exchange_rate": str(req.exchange_rate),
        "commission_amount": str(req.commission_amount_display),
        "commission_percent": str(req.commission_percent),
        "pair": f"{req.offer_asset.unit_name}/{req.request_asset.unit_name}",
        "note": req.note,
        "response_note": req.response_note,
        "created_at": req.created_at.isoformat(),
        "responded_at": req.responded_at.isoformat() if req.responded_at else None,
    }


@method_decorator(csrf_exempt, name="dispatch")
class BourseCenterApiView(CorsApiView):
    def get(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)

        user = request.user

        incoming = list(
            AssetExchangeRequest.objects
            .filter(counterparty=user)
            .select_related("requester", "requester_account", "counterparty_account", "offer_asset", "request_asset")
            .order_by("-created_at")[:30]
        )
        outgoing = list(
            AssetExchangeRequest.objects
            .filter(requester=user)
            .select_related("counterparty", "requester_account", "counterparty_account", "offer_asset", "request_asset")
            .order_by("-created_at")[:30]
        )
        public_asks_qs = list(
            AssetExchangeRequest.objects
            .filter(counterparty__isnull=True, status=ExchangeRequestStatus.PENDING)
            .exclude(requester=user)
            .select_related("requester", "requester_account", "offer_asset", "request_asset")
            .order_by("-created_at")[:50]
        )
        recent_trades = list(
            AssetExchangeRequest.objects
            .filter(
                Q(requester=user) | Q(counterparty=user),
                status=ExchangeRequestStatus.ACCEPTED,
            )
            .select_related("requester", "counterparty", "offer_asset", "request_asset")
            .order_by("-responded_at", "-updated_at")[:20]
        )

        # Pair stats
        pairs: dict = {}
        for ask in public_asks_qs:
            key = f"{ask.offer_asset.unit_name}/{ask.request_asset.unit_name}"
            item = pairs.setdefault(key, {
                "pair": key,
                "ask_count": 0,
                "last_rate": str(ask.exchange_rate),
                "offer_unit": ask.offer_asset.unit_name,
                "request_unit": ask.request_asset.unit_name,
                "offer_depth": "0",
            })
            item["ask_count"] += 1
            item["last_rate"] = str(ask.exchange_rate)
            item["offer_depth"] = str(ask.offer_amount_display + Decimal(item["offer_depth"]))
        pair_stats = sorted(pairs.values(), key=lambda x: x["ask_count"], reverse=True)[:8]

        # User accounts with holdings
        accounts = list(
            LedgerAccount.objects
            .filter(user=user, active=True)
            .prefetch_related("holdings__asset")
            .order_by("code")
        )
        accounts_data = []
        for acc in accounts:
            holdings = [
                {"asset": h.asset.unit_name, "asset_name": h.asset.name, "balance": str(h.balance_display)}
                for h in acc.holdings.all()
                if h.balance_base_units > 0 and h.asset.active and h.asset.is_currency
            ][:8]
            accounts_data.append({"id": acc.pk, "code": acc.code, "name": acc.name, "holdings": holdings})

        # Assets for the create form
        assets = list(
            Asset.objects.filter(active=True, is_currency=True).order_by("unit_name")
            .values("id", "unit_name", "name", "decimals")
        )

        return JsonResponse({
            "public_asks": [_ser_request(r, user.pk) for r in public_asks_qs[:24]],
            "proposals": sorted(
                [_ser_request(r, user.pk) for r in incoming + outgoing],
                key=lambda x: x["created_at"],
                reverse=True,
            ),
            "recent_trades": [_ser_request(r, user.pk) for r in recent_trades],
            "pair_stats": pair_stats,
            "accounts": accounts_data,
            "assets": assets,
            "pending_incoming": sum(1 for r in incoming if r.status == ExchangeRequestStatus.PENDING),
            "pending_outgoing": sum(1 for r in outgoing if r.status == ExchangeRequestStatus.PENDING),
        })


@method_decorator(csrf_exempt, name="dispatch")
class BourseCreateApiView(CorsApiView):
    def post(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON."}, status=400)

        try:
            account = LedgerAccount.objects.get(pk=data.get("requester_account_id"), user=request.user, active=True)
            offer_asset = Asset.objects.get(pk=data.get("offer_asset_id"), active=True)
            request_asset = Asset.objects.get(pk=data.get("request_asset_id"), active=True)
        except (LedgerAccount.DoesNotExist, Asset.DoesNotExist):
            return JsonResponse({"error": "Invalid account or asset."}, status=400)

        if offer_asset.pk == request_asset.pk:
            return JsonResponse({"error": "Offer and request assets must differ."}, status=400)

        try:
            offer_amount = Decimal(str(data.get("offer_amount", "")))
            expected_amount = Decimal(str(data.get("expected_amount", "")))
        except InvalidOperation:
            return JsonResponse({"error": "Invalid amounts."}, status=400)

        if offer_amount <= 0 or expected_amount <= 0:
            return JsonResponse({"error": "Amounts must be positive."}, status=400)

        counterparty = None
        counterparty_id = data.get("counterparty_id")
        if counterparty_id:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            try:
                counterparty = User.objects.get(pk=counterparty_id)
            except User.DoesNotExist:
                return JsonResponse({"error": "Counterparty not found."}, status=400)

        req = AssetExchangeRequest.objects.create(
            requester=request.user,
            counterparty=counterparty,
            requester_account=account,
            offer_asset=offer_asset,
            request_asset=request_asset,
            offer_amount_base_units=to_base_units(offer_amount, offer_asset.decimals),
            request_amount_base_units=to_base_units(expected_amount, request_asset.decimals),
            commission_amount_base_units=0,
            exchange_rate=expected_amount / offer_amount,
            commission_percent=Decimal("0"),
            note=str(data.get("note", "")),
            metadata={"source": "enigma"},
        )
        return JsonResponse({"ok": True, "id": req.pk})


@method_decorator(csrf_exempt, name="dispatch")
class BourseAcceptApiView(CorsApiView):
    def post(self, request, pk):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)

        from toto.bazaar.wallet_pin import has_wallet_pin, session_is_verified, verify_pin_token
        from toto.bourse.services import accept_exchange_request
        from django.core.exceptions import ValidationError

        try:
            body = json.loads(request.body) if request.body else {}
        except (json.JSONDecodeError, ValueError):
            body = {}

        try:
            req = AssetExchangeRequest.objects.select_related(
                "counterparty", "requester", "offer_asset", "request_asset"
            ).get(
                Q(counterparty=request.user) | (Q(counterparty__isnull=True) & ~Q(requester=request.user)),
                pk=pk, status=ExchangeRequestStatus.PENDING,
            )
        except AssetExchangeRequest.DoesNotExist:
            return JsonResponse({"error": "Not found."}, status=404)

        if not has_wallet_pin(request.user):
            return JsonResponse({"error": "Set a wallet PIN first.", "no_pin": True}, status=400)

        pin_token = body.get("pin_token", "")
        if not (pin_token and verify_pin_token(pin_token, request.user)) and not session_is_verified(request.session):
            return JsonResponse({"error": "PIN verification required.", "pin_required": True}, status=403)

        account_id = body.get("counterparty_account_id")
        if not account_id:
            return JsonResponse({"error": "counterparty_account_id required."}, status=400)
        try:
            account = LedgerAccount.objects.get(pk=account_id, user=request.user, active=True)
        except LedgerAccount.DoesNotExist:
            return JsonResponse({"error": "Account not found."}, status=400)

        try:
            accept_exchange_request(
                exchange_request=req,
                counterparty_account=account,
                response_note=body.get("response_note", ""),
            )
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            return JsonResponse({"error": msg}, status=400)

        return JsonResponse({"ok": True})


@method_decorator(csrf_exempt, name="dispatch")
class BourseRejectApiView(CorsApiView):
    def post(self, request, pk):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        from toto.bourse.services import reject_exchange_request
        try:
            body = json.loads(request.body) if request.body else {}
        except (json.JSONDecodeError, ValueError):
            body = {}
        try:
            req = AssetExchangeRequest.objects.get(
                Q(counterparty=request.user) | (Q(counterparty__isnull=True) & ~Q(requester=request.user)),
                pk=pk, status=ExchangeRequestStatus.PENDING,
            )
        except AssetExchangeRequest.DoesNotExist:
            return JsonResponse({"error": "Not found."}, status=404)
        reject_exchange_request(exchange_request=req, response_note=body.get("response_note", ""))
        return JsonResponse({"ok": True})


@method_decorator(csrf_exempt, name="dispatch")
class BourseCancelApiView(CorsApiView):
    def post(self, request, pk):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        from toto.bourse.services import cancel_exchange_request
        try:
            req = AssetExchangeRequest.objects.get(pk=pk, requester=request.user, status=ExchangeRequestStatus.PENDING)
        except AssetExchangeRequest.DoesNotExist:
            return JsonResponse({"error": "Not found."}, status=404)
        cancel_exchange_request(exchange_request=req)
        return JsonResponse({"ok": True})
