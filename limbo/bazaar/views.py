from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import ValidationError
from django.db.models import Count, Sum, Avg, Q
from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import TemplateView, ListView, DetailView, FormView, CreateView, UpdateView
from toto.core.page import PageProcessor
from .forms import AddToCartForm, CheckoutForm, ProductForm, ReviewForm, ShipmentForm
from .models import (
    Product, ProductCategory, Vendor, CartItem, Order, OrderItem, Shipment, ProductReview,
    ShipmentLocation, WalletPin,
)
from .selectors import active_shop, published_products, get_current_cart, customer_orders, product_review_metrics, vendor_inventory_metrics
from .services import (
    add_product_to_cart, create_order_from_cart, apply_coupon,
    pay_order_with_ledger, get_user_ledger_sources, get_stablecoin_for_order_currency,
    get_wallet_payment_sources, create_obligation_for_order, fulfill_order_obligation,
)
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
        context['featured_products'] = published_products(shop).filter(is_featured=True).exclude(product_type='service')[:8]
        context['new_products'] = published_products(shop).exclude(product_type='service')[:8]
        context['categories'] = ProductCategory.objects.filter(shop=shop, is_active=True, parent__isnull=True)[:12] if shop else []
        context['vendors'] = Vendor.objects.filter(shop=shop, status='active')[:8] if shop else []
        context['services'] = published_products(shop).filter(product_type='service')[:8] if shop else []
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
        try:
            add_product_to_cart(request, product, variant, quantity)
        except ValueError as exc:
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
            messages.error(request, str(exc))
            return redirect(request.POST.get('next') or 'bazaar:product-list')
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


class CheckoutView(BazaarContextMixin, LoginRequiredMixin, FormView):
    template_name = 'bazaar/checkout.html'
    form_class = CheckoutForm

    def _user_default_address(self):
        user = self.request.user
        if not user.is_authenticated:
            return None
        person = getattr(user, 'community_profile', None)
        if person and person.address_id:
            return person.address
        return None

    def get_initial(self):
        initial = super().get_initial()
        addr = self._user_default_address()
        if addr:
            initial['shipping_address'] = addr.pk
        return initial

    def get_context_data(self, **kwargs):
        import json as _json
        context = super().get_context_data(**kwargs)
        default_address = self._user_default_address()
        context['default_address'] = default_address
        user = self.request.user
        if user.is_authenticated:
            saved_locations = (
                ShipmentLocation.objects
                .filter(user=user)
                .select_related('address')
            )
        else:
            saved_locations = ShipmentLocation.objects.none()
        context['saved_locations'] = saved_locations

        from .wallet_pin import has_wallet_pin
        context['wallet_secured'] = has_wallet_pin(user) if user.is_authenticated else False
        context['person'] = getattr(user, 'community_profile', None) if user.is_authenticated else None

        def _geom(addr):
            if addr and addr.geometry:
                return {'lat': addr.geometry.y, 'lng': addr.geometry.x}
            return None

        context['checkout_payload_json'] = _json.dumps({
            'default': {
                'label': str(default_address),
                'coords': _geom(default_address),
            } if default_address else None,
            'saved': [
                {
                    'pk': loc.pk,
                    'label': loc.label or str(loc.address),
                    'address': str(loc.address),
                    'coords': _geom(loc.address),
                }
                for loc in saved_locations
            ],
        })
        return context

    def form_valid(self, form):
        from toto.locations.models import Address
        from django.contrib.gis.geos import Point

        from .wallet_pin import has_wallet_pin
        from django.http import HttpResponseForbidden
        if not has_wallet_pin(self.request.user):
            return HttpResponseForbidden('Wallet PIN not set. Please secure your wallet before placing orders.')

        cart = get_current_cart(self.request)
        user = self.request.user
        email = user.email if user.is_authenticated else ''

        d = form.cleaned_data
        shipping_address = None

        if form.has_new_address():
            addr = Address(
                country_name=(d.get('new_country') or '').upper()[:2],
                state_or_province_name=d.get('new_state') or '',
                locality_name=d.get('new_locality') or '',
                street=d.get('new_street') or '',
                building=d.get('new_building') or '',
                apartment=d.get('new_apartment') or None,
            )
            lat, lng = d.get('new_latitude'), d.get('new_longitude')
            if lat is not None and lng is not None:
                addr.geometry = Point(lng, lat, srid=4326)
            addr.save()
            if user.is_authenticated and d.get('save_location'):
                ShipmentLocation.objects.create(
                    user=user,
                    address=addr,
                    label=d.get('location_label') or '',
                    is_default=not ShipmentLocation.objects.filter(user=user).exists(),
                )
            shipping_address = addr
        elif d.get('selected_location') and user.is_authenticated:
            loc = ShipmentLocation.objects.filter(pk=d['selected_location'], user=user).select_related('address').first()
            if loc:
                shipping_address = loc.address
        else:
            addr_pk = d.get('shipping_address')
            shipping_address = Address.objects.filter(pk=addr_pk).first() if addr_pk else None

        checkout_data = {
            **d,
            'email': email,
            'shipping_address': shipping_address,
            'billing_address': shipping_address,
        }
        checkout_data['payment_method'] = 'transfer'
        try:
            order = create_order_from_cart(cart, checkout_data)
        except ValueError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        return redirect('bazaar:order-detail', order_number=order.order_number)


class CheckoutReverseGeocodeView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        from toto.locations.geocode import reverse_geocode_address

        lat = request.GET.get('lat')
        lng = request.GET.get('lng')
        if lat in (None, '') or lng in (None, ''):
            return JsonResponse({'ok': False, 'error': 'Coordinates are required.'}, status=400)

        address = reverse_geocode_address(lat, lng)
        return JsonResponse({'ok': True, 'address': address})


class OrderConfirmationView(BazaarContextMixin, DetailView):
    model = Order
    template_name = 'bazaar/order_confirmation.html'
    context_object_name = 'order'
    slug_field = 'order_number'
    slug_url_kwarg = 'order_number'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.is_authenticated and self.object.payment_status != 'paid':
            sources, preferred = get_wallet_payment_sources(self.request.user, self.object)
            context['wallet_payment_sources'] = sources
            context['preferred_asset'] = preferred
        if self.request.user.is_authenticated:
            from toto.assets.models import LedgerAccount
            context['wallet_accounts'] = (
                LedgerAccount.objects
                .filter(user=self.request.user, active=True)
                .prefetch_related('holdings__asset')
            )
        return context


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
    def get_context_data(self, **kwargs):
        import json as _json
        context = super().get_context_data(**kwargs)
        shipments = self.object.shipments.select_related(
            'shipping_method', 'vendor', 'origin_address', 'destination_address',
            'logistics_package',
        ).order_by('id')
        context['shipments'] = shipments

        def _geom(addr):
            if addr and addr.geometry:
                return _json.loads(addr.geometry.geojson)
            return None

        shipping_addr = self.object.shipping_address

        def _pkg_location(shipment):
            pkg = getattr(shipment, 'logistics_package', None)
            if not pkg:
                return None
            last = pkg.events.select_related('location').order_by('-occurred_at').first()
            if last and last.location:
                return {'label': str(last.location), 'geometry': _geom(last.location)}
            return None

        payload = {
            'order_number': self.object.order_number,
            'delivery': {'label': str(shipping_addr), 'geometry': _geom(shipping_addr)} if shipping_addr else None,
            'shipments': [
                {
                    'id': s.pk,
                    'carrier': s.carrier,
                    'status_display': s.get_status_display(),
                    'origin': {'label': str(s.origin_address), 'geometry': _geom(s.origin_address)} if s.origin_address else None,
                    'destination': {'label': str(s.destination_address), 'geometry': _geom(s.destination_address)} if s.destination_address else None,
                    'current_location': _pkg_location(s),
                }
                for s in shipments
            ],
        }
        context['payload_json'] = _json.dumps(payload)
        context['tribunal_cases'] = self.object.tribunal_cases.order_by('-opened_at')

        if self.object.payment_status != 'paid':
            sources, preferred = get_wallet_payment_sources(self.request.user, self.object)
            context['wallet_payment_sources'] = sources
            context['preferred_asset'] = preferred
        if self.request.user.is_authenticated:
            from toto.assets.models import LedgerAccount
            context['wallet_accounts'] = (
                LedgerAccount.objects
                .filter(user=self.request.user, active=True)
                .prefetch_related('holdings__asset')
            )
        return context


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


class ShipmentCreateView(BazaarContextMixin, LoginRequiredMixin, CreateView):
    model = Shipment
    form_class = ShipmentForm
    template_name = 'bazaar/vendor/shipment_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['order'] = get_object_or_404(Order, order_number=self.kwargs['order_number'])
        context['creating'] = True
        return context

    def form_valid(self, form):
        order = get_object_or_404(Order, order_number=self.kwargs['order_number'])
        vendor = require_vendor(self.request.user)
        form.instance.order = order
        form.instance.vendor = vendor
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('bazaar:vendor-orders')


class ShipmentTrackingView(BazaarContextMixin, DetailView):
    model = Order
    template_name = 'bazaar/customer/shipment_tracking.html'
    context_object_name = 'order'
    slug_field = 'order_number'
    slug_url_kwarg = 'order_number'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['shipments'] = self.object.shipments.select_related(
            'vendor', 'shipping_method', 'origin_address', 'destination_address'
        ).order_by('-id')
        return context


class LedgerPaymentView(LoginRequiredMixin, View):
    def post(self, request, order_number):
        from .wallet_pin import has_wallet_pin, session_is_verified
        next_url = request.POST.get('next') or reverse('bazaar:order-detail', kwargs={'order_number': order_number})

        if not has_wallet_pin(request.user):
            return JsonResponse({'ok': False, 'error': 'No wallet PIN set. Please set a PIN before making payments.', 'setup_url': reverse('assets:wallet_pin_set')}, status=403)
        if not session_is_verified(request.session):
            return JsonResponse({'ok': False, 'error': 'Wallet PIN required. Please verify your PIN.'}, status=403)

        order = get_object_or_404(Order, order_number=order_number)
        if order.user and order.user != request.user:
            return JsonResponse({'ok': False, 'error': 'You do not have permission to pay this order.'}, status=403)

        from toto.assets.models import LedgerAccount, Asset
        account_id = request.POST.get('account_id')
        asset_id   = request.POST.get('asset_id')

        try:
            account = LedgerAccount.objects.get(pk=account_id, user=request.user, active=True)
            asset   = Asset.objects.get(pk=asset_id, active=True)
            pay_order_with_ledger(order, account, asset)
            order.refresh_from_db()
            paid_amount = (order.metadata or {}).get('payment_amount', order.total_amount)
            return JsonResponse({'ok': True, 'redirect': next_url, 'message': f'Payment of {paid_amount} {asset.unit_name} confirmed.'})
        except (LedgerAccount.DoesNotExist, Asset.DoesNotExist):
            return JsonResponse({'ok': False, 'error': 'Invalid account or asset. Please try again.'}, status=400)
        except (ValueError, ValidationError) as exc:
            return JsonResponse({'ok': False, 'error': str(exc)}, status=400)


class OnDeliveryPaymentView(LoginRequiredMixin, View):
    """Create an Obligation for on-delivery deferred payment."""
    def post(self, request, order_number):
        from .wallet_pin import has_wallet_pin, session_is_verified
        from django.utils import timezone as tz
        from datetime import timedelta
        next_url = request.POST.get('next') or reverse('bazaar:order-detail', kwargs={'order_number': order_number})

        if not has_wallet_pin(request.user):
            return JsonResponse({'ok': False, 'error': 'No wallet PIN set. Please set a PIN before making payments.', 'setup_url': reverse('assets:wallet_pin_set')}, status=403)
        if not session_is_verified(request.session):
            return JsonResponse({'ok': False, 'error': 'Wallet PIN required. Please verify your PIN.'}, status=403)

        order = get_object_or_404(Order, order_number=order_number)
        if order.user and order.user != request.user:
            return JsonResponse({'ok': False, 'error': 'You do not have permission to do this.'}, status=403)

        from toto.assets.models import LedgerAccount, Asset
        account_id = request.POST.get('account_id')
        asset_id   = request.POST.get('asset_id')
        days = int(request.POST.get('due_days', 14))

        try:
            account = LedgerAccount.objects.get(pk=account_id, user=request.user, active=True)
            asset   = Asset.objects.get(pk=asset_id, active=True, is_currency=True)
            due_at  = tz.now() + timedelta(days=days)
            create_obligation_for_order(order, account, asset, due_at)
            return JsonResponse({'ok': True, 'redirect': next_url, 'message': f'On-delivery obligation created. Due by {due_at.strftime("%Y-%m-%d")}.'})
        except (LedgerAccount.DoesNotExist, Asset.DoesNotExist):
            return JsonResponse({'ok': False, 'error': 'Invalid account or asset. Please try again.'}, status=400)
        except (ValueError, ValidationError) as exc:
            return JsonResponse({'ok': False, 'error': str(exc)}, status=400)


class FulfillObligationView(LoginRequiredMixin, View):
    """Pay off an outstanding on-delivery obligation."""
    def post(self, request, order_number):
        order = get_object_or_404(Order, order_number=order_number)
        if order.user and order.user != request.user:
            messages.error(request, 'You do not have permission to do this.')
            return redirect('bazaar:order-detail', order_number=order_number)

        try:
            fulfill_order_obligation(order)
            messages.success(request, 'Payment fulfilled successfully.')
        except ValueError as exc:
            messages.error(request, str(exc))

        return redirect('bazaar:order-detail', order_number=order_number)


class ShipmentMapView(BazaarContextMixin, DetailView):
    """Shipment route map — mirrors the travels/travel_review style."""
    model = Order
    template_name = 'bazaar/customer/shipment_map.html'
    context_object_name = 'order'
    slug_field = 'order_number'
    slug_url_kwarg = 'order_number'

    def get_context_data(self, **kwargs):
        import json as _json
        context = super().get_context_data(**kwargs)
        shipments = self.object.shipments.select_related(
            'origin_address', 'destination_address', 'vendor', 'shipping_method'
        ).order_by('id')
        context['shipments'] = shipments

        def geom(address):
            if address and address.geometry:
                return _json.loads(address.geometry.geojson)
            return None

        payload = {
            'order_number': self.object.order_number,
            'shipments': [
                {
                    'id': s.pk,
                    'carrier': s.carrier,
                    'status': s.status,
                    'status_display': s.get_status_display(),
                    'tracking_number': s.tracking_number,
                    'origin': {'label': str(s.origin_address), 'geometry': geom(s.origin_address)} if s.origin_address else None,
                    'destination': {'label': str(s.destination_address), 'geometry': geom(s.destination_address)} if s.destination_address else None,
                }
                for s in shipments
            ],
        }
        context['payload_json'] = _json.dumps(payload)
        return context


class WalletPinVerifyView(LoginRequiredMixin, View):
    """AJAX endpoint — verifies wallet PIN and stamps the session."""
    def post(self, request):
        import json as _json
        from .wallet_pin import check_wallet_pin, mark_session_verified
        try:
            data = _json.loads(request.body)
            raw_pin = data.get('pin', '')
        except Exception:
            return JsonResponse({'ok': False, 'error': 'Invalid request.'}, status=400)

        if not raw_pin:
            return JsonResponse({'ok': False, 'error': 'PIN is required.'})

        if check_wallet_pin(request.user, raw_pin):
            mark_session_verified(request.session)
            return JsonResponse({'ok': True})
        return JsonResponse({'ok': False, 'error': 'Incorrect PIN.'})


class WalletPinSetView(LoginRequiredMixin, BazaarContextMixin, TemplateView):
    """Set or update the wallet PIN."""
    template_name = 'bazaar/wallet_pin_set.html'

    def post(self, request):
        from .wallet_pin import set_wallet_pin
        pin = request.POST.get('pin', '').strip()
        confirm = request.POST.get('pin_confirm', '').strip()
        if not pin:
            messages.error(request, 'PIN cannot be empty.')
            return self.get(request)
        if len(pin) < 4:
            messages.error(request, 'PIN must be at least 4 characters.')
            return self.get(request)
        if pin != confirm:
            messages.error(request, 'PINs do not match.')
            return self.get(request)
        try:
            set_wallet_pin(request.user, pin)
            messages.success(request, 'Wallet PIN set successfully.')
        except Exception as exc:
            messages.error(request, f'Could not save PIN: {exc}')
        return redirect('bazaar:wallet-pin-set')

