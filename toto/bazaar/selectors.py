from django.db.models import Avg, Count, Sum
from .models import Product, Shop, Vendor, Cart, Order, OrderItem, ProductReview


def active_shop():
    return Shop.objects.filter(is_active=True).order_by('name').first()


def published_products(shop=None):
    qs = Product.objects.published().select_related('shop', 'vendor', 'category').prefetch_related('images', 'reviews')
    if shop:
        qs = qs.filter(shop=shop)
    return qs


def product_review_metrics(product):
    qs = product.reviews.filter(status='published')
    data = qs.aggregate(avg_rating=Avg('rating'), review_count=Count('id'))
    data['avg_rating'] = round(data['avg_rating'] or 0, 1)
    return data


def vendor_inventory_metrics(vendor):
    products = vendor.products.all()
    return {
        'product_count': products.count(),
        'published_count': products.filter(status='published').count(),
        'pending_count': products.filter(status='pending_review').count(),
        'low_stock_count': products.filter(stock_tracking_enabled=True, stock_quantity__lte=5).count(),
        'order_item_count': vendor.order_items.count(),
        'sales_total': vendor.order_items.aggregate(total=Sum('total_price'))['total'] or 0,
    }


def get_current_cart(request, shop=None):
    if shop is None:
        shop = active_shop()
    if not shop:
        return None
    if request.user.is_authenticated:
        return Cart.objects.filter(user=request.user, shop=shop, status='active').prefetch_related('items__product', 'items__variant').first()
    key = request.session.session_key
    if not key:
        request.session.create()
        key = request.session.session_key
    return Cart.objects.filter(session_key=key, shop=shop, status='active').prefetch_related('items__product', 'items__variant').first()


def customer_orders(user):
    return Order.objects.filter(user=user).select_related('shop').prefetch_related('items').order_by('-created_at')
