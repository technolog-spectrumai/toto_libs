from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db.models import Count, Sum, Avg, Q
from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import TemplateView, ListView, DetailView, FormView, CreateView, UpdateView
from toto.core.page import PageProcessor
from .forms import AddToCartForm, CheckoutForm, ProductForm, ReviewForm, ShipmentForm
from .models import Product, ProductCategory, Vendor, CartItem, Order, OrderItem, Shipment, ProductReview
from .selectors import active_shop, published_products, get_current_cart, customer_orders, product_review_metrics, vendor_inventory_metrics
from .services import add_product_to_cart, create_order_from_cart, apply_coupon
from .permissions import require_vendor


class BazaarContextMixin:
    def decorate(self, context):
        context.setdefault('shop', active_shop())
        context.setdefault('cart', get_current_cart(self.request, context.get('shop')))
        return PageProcessor().decorate(context, self.request)

    def get_context_data(self, **kwargs):
        return self.decorate(super().get_context_data(**kwargs))


class ShopHomeView(BazaarContextMixin, TemplateView):
    template_name = 'bazaar/shop_home.html'
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        shop = context.get('shop')
        context['featured_products'] = published_products(shop).filter(is_featured=True)[:8]
        context['new_products'] = published_products(shop)[:8]
        context['categories'] = ProductCategory.objects.filter(shop=shop, is_active=True, parent__isnull=True)[:12] if shop else []
        context['vendors'] = Vendor.objects.filter(shop=shop, status='active')[:8] if shop else []
        return context


class ProductListView(BazaarContextMixin, ListView):
    model = Product
    template_name = 'bazaar/product_list.html'
    context_object_name = 'products'
    paginate_by = 24
    def get_queryset(self):
        shop = active_shop()
        qs = published_products(shop)
        q = self.request.GET.get('q')
        category = self.request.GET.get('category')
        vendor = self.request.GET.get('vendor')
        product_type = self.request.GET.get('type')
        sort = self.request.GET.get('sort', 'newest')
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(summary__icontains=q) | Q(description__icontains=q))
        if category:
            qs = qs.filter(category__slug=category)
        if vendor:
            qs = qs.filter(vendor__slug=vendor)
        if product_type:
            qs = qs.filter(product_type=product_type)
        if sort == 'price_asc': qs = qs.order_by('price')
        elif sort == 'price_desc': qs = qs.order_by('-price')
        elif sort == 'popular': qs = qs.annotate(order_count=Count('order_items')).order_by('-order_count')
        return qs
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        shop = context.get('shop')
        context['categories'] = ProductCategory.objects.filter(shop=shop, is_active=True) if shop else []
        context['vendors'] = Vendor.objects.filter(shop=shop, status='active') if shop else []
        context['filters'] = self.request.GET
        return context


class ProductDetailView(BazaarContextMixin, DetailView):
    model = Product
    template_name = 'bazaar/product_detail.html'
    context_object_name = 'product'
    slug_field = 'slug'
    def get_queryset(self):
        return published_products().prefetch_related('variants', 'images', 'reviews')
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        product = self.object
        context['review_metrics'] = product_review_metrics(product)
        context['reviews'] = product.reviews.filter(status='published')[:10]
        context['related_products'] = published_products(product.shop).filter(category=product.category).exclude(pk=product.pk)[:4]
        context['add_to_cart_form'] = AddToCartForm(initial={'product_id': product.pk, 'quantity': 1})
        return context


class CategoryDetailView(BazaarContextMixin, DetailView):
    model = ProductCategory
    template_name = 'bazaar/category_detail.html'
    context_object_name = 'category'
    slug_field = 'slug'
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['products'] = published_products(self.object.shop).filter(category=self.object)
        return context


class VendorStorefrontView(BazaarContextMixin, DetailView):
    model = Vendor
    template_name = 'bazaar/vendor_storefront.html'
    context_object_name = 'vendor'
    slug_field = 'slug'
    def get_queryset(self):
        return Vendor.objects.filter(status='active').select_related('shop', 'location', 'community', 'person')
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['products'] = published_products(self.object.shop).filter(vendor=self.object)
        context['metrics'] = vendor_inventory_metrics(self.object)
        context['reviews'] = self.object.reviews.all()[:10]
        return context


class CartDetailView(BazaarContextMixin, TemplateView):
    template_name = 'bazaar/cart_detail.html'
    def post(self, request, *args, **kwargs):
        cart = get_current_cart(request)
        code = request.POST.get('coupon_code')
        if cart and code:
            try:
                apply_coupon(cart, code)
                messages.success(request, 'Coupon applied.')
            except ValueError as exc:
                messages.error(request, str(exc))
        return redirect('bazaar:cart')


class AddToCartView(View):
    def post(self, request, *args, **kwargs):
        product = get_object_or_404(Product.objects.published(), pk=request.POST.get('product_id'))
        variant = product.variants.filter(pk=request.POST.get('variant_id')).first() if request.POST.get('variant_id') else None
        quantity = int(request.POST.get('quantity') or 1)
        add_product_to_cart(request, product, variant, quantity)
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'ok': True})
        messages.success(request, f'Added {product.name} to cart.')
        return redirect(request.POST.get('next') or product.get_absolute_url() if hasattr(product, 'get_absolute_url') else 'bazaar:cart')


class UpdateCartItemView(View):
    def post(self, request, pk, *args, **kwargs):
        cart = get_current_cart(request)
        item = get_object_or_404(CartItem, pk=pk, cart=cart)
        quantity = int(request.POST.get('quantity') or 0)
        if quantity <= 0:
            item.delete()
        else:
            item.quantity = quantity
            item.save(update_fields=['quantity', 'updated_at'])
        return redirect('bazaar:cart')


class CheckoutView(BazaarContextMixin, FormView):
    template_name = 'bazaar/checkout.html'
    form_class = CheckoutForm
    def form_valid(self, form):
        cart = get_current_cart(self.request)
        try:
            order = create_order_from_cart(cart, form.cleaned_data)
        except ValueError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        return redirect('bazaar:order-confirmation', order_number=order.order_number)


class OrderConfirmationView(BazaarContextMixin, DetailView):
    model = Order
    template_name = 'bazaar/order_confirmation.html'
    context_object_name = 'order'
    slug_field = 'order_number'
    slug_url_kwarg = 'order_number'


class CustomerOrderListView(BazaarContextMixin, LoginRequiredMixin, ListView):
    model = Order
    template_name = 'bazaar/customer/order_list.html'
    context_object_name = 'orders'
    paginate_by = 20
    def get_queryset(self):
        return customer_orders(self.request.user)


class CustomerOrderDetailView(BazaarContextMixin, LoginRequiredMixin, DetailView):
    model = Order
    template_name = 'bazaar/customer/order_detail.html'
    context_object_name = 'order'
    slug_field = 'order_number'
    slug_url_kwarg = 'order_number'
    def get_queryset(self):
        return customer_orders(self.request.user)


class WishlistView(BazaarContextMixin, LoginRequiredMixin, TemplateView):
    template_name = 'bazaar/customer/wishlist.html'
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['wishlists'] = self.request.user.bazaar_wishlists.prefetch_related('items__product')
        return context


class ReviewCreateView(BazaarContextMixin, LoginRequiredMixin, CreateView):
    model = ProductReview
    form_class = ReviewForm
    template_name = 'bazaar/customer/review_form.html'
    def form_valid(self, form):
        product = get_object_or_404(Product, pk=self.kwargs['product_pk'])
        form.instance.product = product
        form.instance.user = self.request.user
        form.instance.status = 'pending'
        return super().form_valid(form)
    def get_success_url(self):
        return reverse('bazaar:product-detail', kwargs={'slug': self.object.product.slug})


class VendorDashboardView(BazaarContextMixin, LoginRequiredMixin, TemplateView):
    template_name = 'bazaar/vendor/dashboard.html'
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        vendor = require_vendor(self.request.user, context.get('shop'))
        context['vendor'] = vendor
        context['metrics'] = vendor_inventory_metrics(vendor)
        context['pending_items'] = vendor.order_items.select_related('order', 'product')[:10]
        return context


class VendorProductListView(BazaarContextMixin, LoginRequiredMixin, ListView):
    model = Product
    template_name = 'bazaar/vendor/product_list.html'
    context_object_name = 'products'
    def get_queryset(self):
        self.vendor = require_vendor(self.request.user)
        return self.vendor.products.select_related('category').order_by('-created_at')
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['vendor'] = self.vendor
        return context


class VendorProductCreateView(BazaarContextMixin, LoginRequiredMixin, CreateView):
    model = Product
    form_class = ProductForm
    template_name = 'bazaar/vendor/product_form.html'
    success_url = reverse_lazy('bazaar:vendor-products')
    def form_valid(self, form):
        vendor = require_vendor(self.request.user)
        form.instance.vendor = vendor
        form.instance.shop = vendor.shop
        return super().form_valid(form)


class VendorProductUpdateView(BazaarContextMixin, LoginRequiredMixin, UpdateView):
    model = Product
    form_class = ProductForm
    template_name = 'bazaar/vendor/product_form.html'
    success_url = reverse_lazy('bazaar:vendor-products')
    def get_queryset(self):
        vendor = require_vendor(self.request.user)
        return vendor.products.all()


class VendorOrderListView(BazaarContextMixin, LoginRequiredMixin, ListView):
    model = OrderItem
    template_name = 'bazaar/vendor/order_list.html'
    context_object_name = 'order_items'
    def get_queryset(self):
        vendor = require_vendor(self.request.user)
        return vendor.order_items.select_related('order', 'product', 'variant').order_by('-order__created_at')


class VendorShipmentUpdateView(BazaarContextMixin, LoginRequiredMixin, UpdateView):
    model = Shipment
    form_class = ShipmentForm
    template_name = 'bazaar/vendor/shipment_form.html'
    success_url = reverse_lazy('bazaar:vendor-orders')
    def get_queryset(self):
        vendor = require_vendor(self.request.user)
        return vendor.shipments.all()


class BazaarAdminDashboardView(BazaarContextMixin, PermissionRequiredMixin, TemplateView):
    permission_required = 'bazaar.view_order'
    template_name = 'bazaar/operator/dashboard.html'
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['total_sales'] = Order.objects.aggregate(total=Sum('total_amount'))['total'] or 0
        context['active_vendors'] = Vendor.objects.filter(status='active').count()
        context['pending_vendors'] = Vendor.objects.filter(status='pending_review').count()
        context['pending_products'] = Product.objects.filter(status='pending_review').count()
        context['failed_orders'] = Order.objects.filter(status='failed')[:10]
        return context


class ProductModerationQueueView(BazaarContextMixin, PermissionRequiredMixin, ListView):
    permission_required = 'bazaar.change_product'
    model = Product
    template_name = 'bazaar/operator/product_moderation_queue.html'
    context_object_name = 'products'
    def get_queryset(self):
        return Product.objects.filter(status='pending_review').select_related('vendor', 'category').order_by('created_at')


class OrderManagementView(BazaarContextMixin, PermissionRequiredMixin, ListView):
    permission_required = 'bazaar.change_order'
    model = Order
    template_name = 'bazaar/operator/order_management.html'
    context_object_name = 'orders'
    paginate_by = 50
    def get_queryset(self):
        qs = Order.objects.select_related('shop', 'user').order_by('-created_at')
        q = self.request.GET.get('q')
        if q:
            qs = qs.filter(Q(order_number__icontains=q) | Q(email__icontains=q))
        return qs
