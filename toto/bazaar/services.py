from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from .models import Cart, CartItem, Order, OrderItem, InventoryMovement, PaymentIntent, Coupon
from .selectors import active_shop


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
    """
    Return a list of dicts for the payment widget.
    Each dict: {account, holding, can_pay, is_preferred}
    """
    preferred = get_stablecoin_for_order_currency(order)
    result = []
    for holding in get_user_ledger_sources(user):
        result.append({
            'account': holding.account,
            'holding': holding,
            'can_pay': holding.balance_display >= order.total_amount,
            'is_preferred': preferred is not None and holding.asset_id == preferred.pk,
        })
    # Sort: preferred first, then by can_pay desc
    result.sort(key=lambda x: (not x['is_preferred'], not x['can_pay']))
    return result, preferred


@transaction.atomic
def pay_order_with_ledger(order, buyer_account, asset):
    from toto.assets.services.assets import transfer_asset
    from toto.assets.queries import get_asset_balance_display

    # Lock the order row — prevents concurrent double-payment
    order = Order.objects.select_for_update().get(pk=order.pk)

    if order.payment_status == 'paid':
        raise ValueError('Order is already paid.')
    if not order.shop.ledger_account:
        raise ValueError('This shop has no ledger account configured for asset payments.')

    balance = get_asset_balance_display(asset, buyer_account)
    if balance < order.total_amount:
        raise ValueError(
            f'Insufficient balance. You have {balance} {asset.unit_name}, '
            f'order total is {order.total_amount} {order.currency}.'
        )

    reference = f'bazaar-{order.order_number}'
    tx = transfer_asset(
        asset=asset,
        sender_account=buyer_account,
        receiver_account=order.shop.ledger_account,
        amount=order.total_amount,
        reference=reference,
        description=f'Payment for Bazaar order {order.order_number}',
        metadata={'bazaar_order': order.order_number, 'shop': order.shop.slug},
    )

    PaymentIntent.objects.create(
        order=order,
        provider='internal_credit',
        provider_reference=reference,
        status='succeeded',
        amount=order.total_amount,
        currency=order.currency,
        metadata={'ledger_tx_reference': reference, 'asset': asset.unit_name},
    )

    order.payment_status = 'paid'
    order.status = 'confirmed'
    order.paid_at = timezone.now()
    order.ledger_account = buyer_account
    order.ledger_asset = asset
    order.ledger_tx_reference = reference
    order.payment_method = 'transfer'
    order.save(update_fields=[
        'payment_status', 'status', 'paid_at', 'updated_at',
        'ledger_account', 'ledger_asset', 'ledger_tx_reference', 'payment_method',
    ])
    return tx


@transaction.atomic
def create_obligation_for_order(order, debtor_account, asset, due_at,
                                collateral_account=None, collateral_asset=None,
                                collateral_amount=Decimal('0')):
    """Create an on-delivery payment obligation for an order."""
    from toto.assets.services.assets import create_obligation

    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.payment_status == 'paid':
        raise ValueError('Order is already paid.')
    if not order.shop.ledger_account:
        raise ValueError('This shop has no ledger account configured.')

    reference = f'oblig-{order.order_number}'
    obligation = create_obligation(
        reference=reference,
        debtor_account=debtor_account,
        creditor_account=order.shop.ledger_account,
        asset=asset,
        amount=order.total_amount,
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
        amount=order.total_amount,
        currency=order.currency,
        metadata={'obligation_reference': reference, 'asset': asset.unit_name, 'due_at': str(due_at)},
    )

    order.payment_status = 'pending'
    order.payment_method = 'on_delivery'
    order.obligation = obligation
    order.ledger_account = debtor_account
    order.ledger_asset = asset
    order.save(update_fields=[
        'payment_status', 'payment_method', 'obligation', 'ledger_account', 'ledger_asset', 'updated_at',
    ])
    return obligation


@transaction.atomic
def fulfill_order_obligation(order):
    """Fulfill the pending obligation tied to an order, marking the order as paid."""
    from toto.assets.services.assets import fulfill_obligation

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

    order.payment_status = 'paid'
    order.status = 'confirmed'
    order.paid_at = timezone.now()
    order.ledger_tx_reference = reference
    order.save(update_fields=['payment_status', 'status', 'paid_at', 'ledger_tx_reference', 'updated_at'])
    return tx


def product_unit_price(product, variant=None):
    if variant and variant.price is not None:
        return variant.price
    return product.price


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
    price = product_unit_price(product, variant)
    item, created = CartItem.objects.get_or_create(
        cart=cart,
        product=product,
        variant=variant,
        defaults={'quantity': quantity, 'unit_price_snapshot': price, 'metadata': metadata or {}},
    )
    if not created:
        item.quantity += quantity
        if metadata:
            item.metadata.update(metadata)
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
