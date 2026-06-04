from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from .models import Cart, CartItem, Order, OrderItem, InventoryMovement, PaymentIntent, Coupon
from .selectors import active_shop


def _accepted_asset_ids_for_order(order):
    """
    Return the set of Asset PKs accepted by all restricting vendors on this order.
    Returns None when no vendor has configured accepted currencies (no restriction).
    Intersects across vendors so the asset must be accepted by every vendor that
    has at least one accepted currency set.
    """
    vendors = {
        item.vendor
        for item in order.items.select_related('vendor').all()
        if item.vendor
    }
    restricted = []
    for vendor in vendors:
        ids = set(vendor.accepted_currencies.values_list('id', flat=True))
        if ids:
            restricted.append(ids)
    if not restricted:
        return None
    result = restricted[0]
    for s in restricted[1:]:
        result &= s
    return result


def get_user_ledger_sources(user):
    """Return holdings the user can pay with — is_currency assets only."""
    from toto.assets.models import AssetHolding, LedgerAccount
    accounts = LedgerAccount.objects.filter(user=user, active=True)
    return (
        AssetHolding.objects
        .filter(account__in=accounts, balance_base_units__gt=0, asset__is_currency=True, asset__active=True)
        .select_related('account', 'asset')
        .order_by('asset__unit_name')
    )


def get_stablecoin_for_order_currency(order):
    """Return the pegged stablecoin Asset for the order's fiat currency, or None."""
    from toto.assets.services.assets import get_stablecoin_for_currency
    return get_stablecoin_for_currency(order.currency)


def get_wallet_payment_sources(user, order):
    """Return wallet assets that can pay this order directly or through an available quote."""
    preferred = get_stablecoin_for_order_currency(order)
    accepted_ids = _accepted_asset_ids_for_order(order)
    result = []
    for holding in get_user_ledger_sources(user):
        if accepted_ids is not None and holding.asset_id not in accepted_ids:
            continue
        quote = None
        error = ""
        try:
            from toto.assets.services.assets import quote_currency_payment
            quote = quote_currency_payment(
                currency_code=order.currency,
                payment_asset=holding.asset,
                amount=order.total_amount,
            )
            can_pay = holding.balance_display >= quote.gross_amount
        except Exception as exc:
            can_pay = False
            error = str(exc)
        result.append({
            'account': holding.account,
            'holding': holding,
            'quote': quote,
            'payment_amount': quote.gross_amount if quote else None,
            'converted_amount': quote.converted_amount if quote else None,
            'commission_amount': quote.commission_amount if quote else None,
            'can_pay': can_pay,
            'exchange_error': error,
            'is_preferred': preferred is not None and holding.asset_id == preferred.pk,
        })
    # Sort: preferred first, then by can_pay desc
    result.sort(key=lambda x: (not x['is_preferred'], not x['can_pay']))
    return result, preferred


@transaction.atomic
def pay_order_with_ledger(order, buyer_account, asset):
    from toto.assets.services.assets import get_exchange_fee_account, quote_currency_payment, transfer_asset
    from toto.assets.queries import get_asset_balance_display

    # Lock the order row — prevents concurrent double-payment
    order = Order.objects.select_for_update().get(pk=order.pk)

    if order.payment_status == 'paid':
        raise ValueError('Order is already paid.')
    if not order.shop.ledger_account:
        raise ValueError('This shop has no ledger account configured for asset payments.')

    accepted_ids = _accepted_asset_ids_for_order(order)
    if accepted_ids is not None and asset.pk not in accepted_ids:
        raise ValueError(f'{asset.unit_name} is not accepted as payment by the vendor(s) on this order.')

    quote = quote_currency_payment(
        currency_code=order.currency,
        payment_asset=asset,
        amount=order.total_amount,
    )

    balance = get_asset_balance_display(asset, buyer_account)
    if balance < quote.gross_amount:
        raise ValueError(
            f'Insufficient balance. You have {balance} {asset.unit_name}, '
            f'payment total is {quote.gross_amount} {asset.unit_name}.'
        )

    reference = f'bazaar-{order.order_number}'
    tx = transfer_asset(
        asset=asset,
        sender_account=buyer_account,
        receiver_account=order.shop.ledger_account,
        amount=quote.converted_amount,
        reference=reference,
        description=f'Payment for Bazaar order {order.order_number}',
        metadata={
            'bazaar_order': order.order_number,
            'shop': order.shop.slug,
            'order_amount': str(order.total_amount),
            'order_currency': order.currency,
            'payment_asset': asset.unit_name,
            'payment_amount': str(quote.gross_amount),
            'converted_amount': str(quote.converted_amount),
            'commission_amount': str(quote.commission_amount),
            'exchange_rate': str(quote.rate),
            'exchange_rate_source': quote.rate_source,
        },
    )
    fee_reference = ""
    if quote.commission_amount > 0:
        fee_reference = f'bazaar-fee-{order.order_number}'
        transfer_asset(
            asset=asset,
            sender_account=buyer_account,
            receiver_account=get_exchange_fee_account(),
            amount=quote.commission_amount,
            reference=fee_reference,
            description=f'Exchange commission for Bazaar order {order.order_number}',
            metadata={
                'bazaar_order': order.order_number,
                'shop': order.shop.slug,
                'payment_reference': reference,
                'commission_percent': str(quote.commission_percent),
            },
        )

    PaymentIntent.objects.create(
        order=order,
        provider='internal_credit',
        provider_reference=reference,
        status='succeeded',
        amount=quote.gross_amount,
        currency=asset.unit_name,
        metadata={
            'ledger_tx_reference': reference,
            'fee_tx_reference': fee_reference,
            'asset': asset.unit_name,
            'order_amount': str(order.total_amount),
            'order_currency': order.currency,
            'converted_amount': str(quote.converted_amount),
            'commission_amount': str(quote.commission_amount),
            'exchange_rate': str(quote.rate),
            'exchange_rate_source': quote.rate_source,
        },
    )

    order.payment_status = 'paid'
    order.status = 'confirmed'
    order.paid_at = timezone.now()
    order.ledger_account = buyer_account
    order.ledger_asset = asset
    order.ledger_tx_reference = reference
    order.payment_method = 'transfer'
    order.metadata = {
        **(order.metadata or {}),
        'payment_amount': str(quote.gross_amount),
        'payment_asset': asset.unit_name,
        'shop_received_amount': str(quote.converted_amount),
        'commission_amount': str(quote.commission_amount),
        'fee_tx_reference': fee_reference,
        'exchange_rate': str(quote.rate),
        'exchange_rate_source': quote.rate_source,
    }
    order.save(update_fields=[
        'payment_status', 'status', 'paid_at', 'updated_at',
        'ledger_account', 'ledger_asset', 'ledger_tx_reference', 'payment_method', 'metadata',
    ])
    return tx


@transaction.atomic
def create_obligation_for_order(order, debtor_account, asset, due_at,
                                collateral_account=None, collateral_asset=None,
                                collateral_amount=Decimal('0')):
    """Create an on-delivery payment obligation for an order."""
    from toto.assets.services.assets import create_obligation, quote_currency_payment

    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.payment_status == 'paid':
        raise ValueError('Order is already paid.')
    if not order.shop.ledger_account:
        raise ValueError('This shop has no ledger account configured.')

    accepted_ids = _accepted_asset_ids_for_order(order)
    if accepted_ids is not None and asset.pk not in accepted_ids:
        raise ValueError(f'{asset.unit_name} is not accepted as payment by the vendor(s) on this order.')

    quote = quote_currency_payment(
        currency_code=order.currency,
        payment_asset=asset,
        amount=order.total_amount,
    )

    reference = f'oblig-{order.order_number}'
    obligation = create_obligation(
        reference=reference,
        debtor_account=debtor_account,
        creditor_account=order.shop.ledger_account,
        asset=asset,
        amount=quote.gross_amount,
        due_at=due_at,
        order_reference=order.order_number,
        collateral_account=collateral_account,
        collateral_asset=collateral_asset,
        collateral_amount=collateral_amount,
    )

    PaymentIntent.objects.create(
        order=order,
        provider='internal_credit',
        provider_reference=reference,
        status='created',
        amount=quote.gross_amount,
        currency=asset.unit_name,
        metadata={
            'obligation_reference': reference,
            'asset': asset.unit_name,
            'due_at': str(due_at),
            'order_amount': str(order.total_amount),
            'order_currency': order.currency,
            'converted_amount': str(quote.converted_amount),
            'commission_amount': str(quote.commission_amount),
            'exchange_rate': str(quote.rate),
            'exchange_rate_source': quote.rate_source,
        },
    )

    order.payment_status = 'pending'
    order.payment_method = 'on_delivery'
    order.obligation = obligation
    order.ledger_account = debtor_account
    order.ledger_asset = asset
    order.metadata = {
        **(order.metadata or {}),
        'obligation_amount': str(quote.gross_amount),
        'obligation_asset': asset.unit_name,
        'shop_receivable_amount': str(quote.converted_amount),
        'commission_amount': str(quote.commission_amount),
        'exchange_rate': str(quote.rate),
        'exchange_rate_source': quote.rate_source,
    }
    order.save(update_fields=[
        'payment_status', 'payment_method', 'obligation', 'ledger_account', 'ledger_asset', 'metadata', 'updated_at',
    ])
    return obligation


@transaction.atomic
def fulfill_order_obligation(order):
    """Fulfill the pending obligation tied to an order, marking the order as paid."""
    from toto.assets.services.assets import fulfill_obligation, get_exchange_fee_account, transfer_asset

    order = Order.objects.select_for_update().get(pk=order.pk)
    if not order.obligation:
        raise ValueError('This order has no obligation to fulfill.')
    if order.payment_status == 'paid':
        raise ValueError('Order is already paid.')

    reference = f'fulfill-{order.order_number}'
    tx = fulfill_obligation(
        obligation=order.obligation,
        reference=reference,
        description=f'Fulfilling on-delivery payment for order {order.order_number}',
    )
    fee_reference = ""
    commission_amount = Decimal((order.metadata or {}).get("commission_amount", "0"))
    if commission_amount > 0 and order.ledger_asset and order.shop.ledger_account:
        fee_reference = f"fulfill-fee-{order.order_number}"
        transfer_asset(
            asset=order.ledger_asset,
            sender_account=order.shop.ledger_account,
            receiver_account=get_exchange_fee_account(),
            amount=commission_amount,
            reference=fee_reference,
            description=f"Exchange commission for fulfilled Bazaar obligation {order.order_number}",
            metadata={"bazaar_order": order.order_number, "obligation_reference": order.obligation.reference},
        )

    order.payment_status = 'paid'
    order.status = 'confirmed'
    order.paid_at = timezone.now()
    order.ledger_tx_reference = reference
    order.metadata = {**(order.metadata or {}), "fee_tx_reference": fee_reference}
    order.save(update_fields=['payment_status', 'status', 'paid_at', 'ledger_tx_reference', 'metadata', 'updated_at'])
    return tx


def product_unit_price(product, variant=None, target_currency=None):
    if variant and variant.price is not None:
        price = variant.price
    else:
        price = product.price

    target_currency = target_currency or product.currency
    if product.currency == target_currency:
        return price

    from toto.assets.services.assets import get_stablecoin_for_currency, quote_exchange
    source_asset = get_stablecoin_for_currency(product.currency)
    target_asset = get_stablecoin_for_currency(target_currency)
    if not source_asset or not target_asset:
        raise ValueError(f'Cannot convert {product.currency} to {target_currency}: missing currency asset peg.')
    quote = quote_exchange(from_asset=source_asset, to_asset=target_asset, amount=price)
    return quote.converted_amount


def get_or_create_cart(request, shop=None):
    shop = shop or active_shop()
    if not shop:
        raise ValueError('No active Bazaar shop exists.')
    if request.user.is_authenticated:
        cart, _ = Cart.objects.get_or_create(user=request.user, shop=shop, status='active', defaults={'currency': shop.currency})
        return cart
    if not request.session.session_key:
        request.session.create()
    cart, _ = Cart.objects.get_or_create(session_key=request.session.session_key, shop=shop, status='active', defaults={'currency': shop.currency})
    return cart


def add_product_to_cart(request, product, variant=None, quantity=1, metadata=None):
    cart = get_or_create_cart(request, product.shop)
    price = product_unit_price(product, variant, target_currency=cart.currency)
    original_price = variant.price if variant and variant.price is not None else product.price
    item_metadata = {
        'priced_currency': product.currency,
        'cart_currency': cart.currency,
        'original_unit_price': str(original_price),
        **(metadata or {}),
    }
    item, created = CartItem.objects.get_or_create(
        cart=cart,
        product=product,
        variant=variant,
        defaults={'quantity': quantity, 'unit_price_snapshot': price, 'metadata': item_metadata},
    )
    if not created:
        item.quantity += quantity
        item.metadata.update(item_metadata)
        item.save(update_fields=['quantity', 'metadata', 'updated_at'])
    return cart, item


def recalculate_cart(cart):
    subtotal = sum((item.line_total for item in cart.items.all()), Decimal('0.00'))
    discount = Decimal('0.00')
    if cart.coupon and cart.coupon.is_active:
        if cart.coupon.discount_type == 'percentage':
            discount = subtotal * (cart.coupon.value / Decimal('100'))
        elif cart.coupon.discount_type == 'fixed_amount':
            discount = min(subtotal, cart.coupon.value)
    return {'subtotal': subtotal, 'discount': discount, 'total': subtotal - discount}


def validate_cart(cart):
    errors = []
    if not cart or not cart.items.exists():
        errors.append('Your cart is empty.')
        return errors
    for item in cart.items.select_related('product', 'variant'):
        if item.product.status != 'published' or not item.product.is_public:
            errors.append(f'{item.product.name} is no longer available.')
        stock = item.variant.stock_quantity if item.variant else item.product.stock_quantity
        tracking = item.product.stock_tracking_enabled
        if tracking and item.quantity > stock:
            errors.append(f'Only {stock} left for {item.product.name}.')
    return errors


@transaction.atomic
def create_order_from_cart(cart, checkout_data):
    errors = validate_cart(cart)
    if errors:
        raise ValueError(' '.join(errors))
    totals = recalculate_cart(cart)
    order = Order.objects.create(
        shop=cart.shop,
        user=cart.user,
        email=checkout_data.get('email') or getattr(cart.user, 'email', ''),
        currency=cart.currency,
        subtotal_amount=totals['subtotal'],
        discount_amount=totals['discount'],
        shipping_amount=checkout_data.get('shipping_amount', Decimal('0.00')),
        tax_amount=checkout_data.get('tax_amount', Decimal('0.00')),
        total_amount=totals['total'] + checkout_data.get('shipping_amount', Decimal('0.00')) + checkout_data.get('tax_amount', Decimal('0.00')),
        billing_address=checkout_data.get('billing_address'),
        shipping_address=checkout_data.get('shipping_address'),
        customer_note=checkout_data.get('customer_note', ''),
    )
    for item in cart.items.select_related('product', 'variant', 'product__vendor'):
        OrderItem.objects.create(
            order=order,
            product=item.product,
            variant=item.variant,
            vendor=item.product.vendor,
            product_name_snapshot=item.product.name,
            sku_snapshot=item.variant.sku if item.variant else '',
            quantity=item.quantity,
            unit_price=item.unit_price_snapshot,
            total_price=item.line_total,
            metadata=item.metadata,
        )
    cart.status = 'converted'
    cart.save(update_fields=['status', 'updated_at'])
    return order


def reserve_inventory(order):
    for item in order.items.select_related('product', 'variant'):
        product = item.product
        if not product or not product.stock_tracking_enabled:
            continue
        if item.variant:
            item.variant.stock_quantity -= item.quantity
            item.variant.save(update_fields=['stock_quantity'])
        else:
            product.stock_quantity -= item.quantity
            product.save(update_fields=['stock_quantity'])
        InventoryMovement.objects.create(product=product, variant=item.variant, movement_type='reservation', quantity=item.quantity, related_order=order)


def mark_order_paid(order, payment_data=None):
    order.payment_status = 'paid'
    order.status = 'confirmed'
    order.paid_at = timezone.now()
    order.save(update_fields=['payment_status', 'status', 'paid_at', 'updated_at'])
    return order


def apply_coupon(cart, coupon_code):
    coupon = Coupon.objects.filter(shop=cart.shop, code__iexact=coupon_code, is_active=True).first()
    if not coupon:
        raise ValueError('Coupon not found or inactive.')
    cart.coupon = coupon
    cart.save(update_fields=['coupon', 'updated_at'])
    return coupon
