from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from .models import Cart, CartItem, Order, OrderItem, InventoryMovement, PaymentIntent, Coupon
from .selectors import active_shop


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
