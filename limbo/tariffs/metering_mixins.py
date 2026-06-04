"""
Shared helpers for metered Django views.

Usage in a view:

    from toto.tariffs.metering_mixins import metered_action

    def upload_file(request):
        tariff, err_response = metered_action(
            request, app_label="vault",
            metric_code="storage.request", quantity=1,
        )
        if err_response:
            return err_response          # renders friendly "top up" page
        # ... do the upload ...
        charge_user(request.user, tariff, "storage.request", 1,
                    source_type="vault.VaultFile", source_id=str(file.pk))
"""
from __future__ import annotations

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import render

from toto.tariffs.charge import (
    InsufficientBalanceError,
    check_user_can_act,
    get_tariff_for_user,
)
from toto.ui import PageProcessor


def metered_action(
    request,
    app_label: str,
    metric_code: str,
    quantity: int | float,
    unit: str = "",
    tariff=None,
    template_on_error: str = "tariffs/partials/insufficient_balance.html",
):
    """
    Resolve tariff for the user, check balance.

    Returns (tariff, None) on success.
    Returns (None, HttpResponse) when user can't afford it — caller returns the response.
    Returns (None, None) when no tariff is configured — action is free, proceed.
    """
    if tariff is None:
        tariff = get_tariff_for_user(request.user, app_label)

    if tariff is None:
        return None, None  # no tariff configured — free pass

    try:
        check_user_can_act(request.user, tariff, metric_code, quantity, unit)
    except InsufficientBalanceError as exc:
        ctx = PageProcessor().decorate({"error": exc}, request)
        return None, render(request, template_on_error, ctx, status=402)

    return tariff, None
