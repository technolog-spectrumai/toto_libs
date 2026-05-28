from decimal import Decimal
from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from toto.core.domain import DomainEntity


def unique_slug_for(instance, field_value, *, scope=None, slug_field='slug'):
    base = slugify(field_value) or instance.__class__.__name__.lower()
    slug = base
    counter = 1
    qs = instance.__class__.objects.all()
    if scope:
        qs = qs.filter(**scope)
    while qs.filter(**{slug_field: slug}).exclude(pk=instance.pk).exists():
        counter += 1
        slug = f'{base}-{counter}'
    return slug


class Shop(DomainEntity):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=280, unique=True, blank=True)
    owner = models.ForeignKey('people.Person', on_delete=models.SET_NULL, null=True, blank=True, related_name='owned_bazaar_shops')
    community = models.ForeignKey('socialhub.Community', on_delete=models.SET_NULL, null=True, blank=True, related_name='bazaar_shops')
    description = models.TextField(blank=True)
    logo = models.ImageField(upload_to='bazaar/shop_logos/', null=True, blank=True)
    cover_image = models.ImageField(upload_to='bazaar/shop_covers/', null=True, blank=True)
    location = models.ForeignKey('locations.Address', on_delete=models.SET_NULL, null=True, blank=True, related_name='bazaar_shops')
    is_active = models.BooleanField(default=True)
    currency = models.CharField(max_length=3, default='PLN')
    default_language = models.CharField(max_length=12, default='pl')
    ledger_account = models.ForeignKey('assets.LedgerAccount', on_delete=models.SET_NULL, null=True, blank=True, related_name='bazaar_shops')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug_for(self, self.name)
        super().save(*args, **kwargs)


class Vendor(DomainEntity):
    STATUS_CHOICES = [('draft','Draft'),('pending_review','Pending review'),('active','Active'),('suspended','Suspended'),('archived','Archived')]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='bazaar_vendor_accounts')
    person = models.ForeignKey('people.Person', on_delete=models.SET_NULL, null=True, blank=True, related_name='bazaar_vendor_profiles')
    community = models.ForeignKey('socialhub.Community', on_delete=models.SET_NULL, null=True, blank=True, related_name='bazaar_vendor_profiles')
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='vendors')
    display_name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=280, blank=True)
    bio = models.TextField(blank=True)
    avatar = models.ImageField(upload_to='bazaar/vendor_avatars/', null=True, blank=True)
    location = models.ForeignKey('locations.Address', on_delete=models.SET_NULL, null=True, blank=True, related_name='bazaar_vendors')
    territory = models.ForeignKey('locations.Territory', on_delete=models.SET_NULL, null=True, blank=True, related_name='bazaar_vendors')
    website = models.URLField(blank=True)
    accepted_currencies = models.ManyToManyField(
        'assets.Asset',
        blank=True,
        related_name='bazaar_vendors',
        limit_choices_to={'active': True},
        help_text="Assets this vendor accepts as payment.",
    )
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='draft')
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['display_name']
        unique_together = [('shop', 'slug')]

    def __str__(self):
        return self.display_name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug_for(self, self.display_name, scope={'shop': self.shop})
        super().save(*args, **kwargs)


class ProductCategory(DomainEntity):
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='categories')
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='children')
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=280, blank=True)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to='bazaar/category_images/', null=True, blank=True)
    position = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['position', 'name']
        unique_together = [('shop', 'slug')]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug_for(self, self.name, scope={'shop': self.shop})
        super().save(*args, **kwargs)


class ProductQuerySet(models.QuerySet):
    def published(self):
        return self.filter(status='published', is_public=True, shop__is_active=True)


class Product(DomainEntity):
    PRODUCT_TYPE_CHOICES = [('physical','Physical'),('digital','Digital'),('service','Service'),('booking','Booking'),('subscription','Subscription'),('bundle','Bundle'),('quote_based','Quote based')]
    STATUS_CHOICES = [('draft','Draft'),('pending_review','Pending review'),('published','Published'),('hidden','Hidden'),('sold_out','Sold out'),('archived','Archived')]
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='products')
    vendor = models.ForeignKey(Vendor, on_delete=models.SET_NULL, null=True, blank=True, related_name='products')
    category = models.ForeignKey(ProductCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='products')
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=280, blank=True)
    summary = models.CharField(max_length=500, blank=True)
    description = models.TextField(blank=True)
    product_type = models.CharField(max_length=32, choices=PRODUCT_TYPE_CHOICES, default='physical')
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='draft')
    price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    compare_at_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default='PLN')
    stock_tracking_enabled = models.BooleanField(default=False)
    stock_quantity = models.IntegerField(default=0)
    origin_address = models.ForeignKey('locations.Address', on_delete=models.SET_NULL, null=True, blank=True, related_name='origin_products')
    pickup_address = models.ForeignKey('locations.Address', on_delete=models.SET_NULL, null=True, blank=True, related_name='pickup_products')
    pickup_available = models.BooleanField(default=False)
    delivery_zones = models.ManyToManyField('locations.Zone', blank=True, related_name='bazaar_products')
    event = models.ForeignKey('events.ScheduledEvent', on_delete=models.SET_NULL, null=True, blank=True, related_name='bazaar_products')
    asset = models.ForeignKey('assets.Asset', on_delete=models.SET_NULL, null=True, blank=True, related_name='bazaar_products')
    is_featured = models.BooleanField(default=False)
    is_public = models.BooleanField(default=True)
    is_regulated = models.BooleanField(default=False, help_text='Requires custodian approval before publishing or order confirmation.')
    custodian = models.ForeignKey(
        'MarketCustodian', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='regulated_products',
    )
    custodian_approval_status = models.CharField(
        max_length=16,
        choices=[('not_required', 'Not required'), ('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')],
        default='not_required',
    )
    custodian_note = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(null=True, blank=True)
    objects = ProductQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at']
        unique_together = [('shop', 'slug')]
        indexes = [models.Index(fields=['shop','status','is_public']), models.Index(fields=['product_type']), models.Index(fields=['is_featured']), models.Index(fields=['is_regulated', 'custodian_approval_status'])]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug_for(self, self.name, scope={'shop': self.shop})
        if self.status == 'published' and not self.published_at:
            self.published_at = timezone.now()
        super().save(*args, **kwargs)


class ProductImage(DomainEntity):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='bazaar/product_images/')
    alt_text = models.CharField(max_length=255, blank=True)
    position = models.PositiveIntegerField(default=0)
    is_primary = models.BooleanField(default=False)
    class Meta: ordering = ['position', 'id']


class ProductVariant(DomainEntity):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='variants')
    name = models.CharField(max_length=255)
    sku = models.CharField(max_length=128, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    stock_quantity = models.IntegerField(default=0)
    attributes = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)


class InventoryMovement(DomainEntity):
    MOVEMENT_TYPE_CHOICES = [('stock_in','Stock in'),('stock_out','Stock out'),('reservation','Reservation'),('release','Release'),('adjustment','Adjustment'),('return','Return')]
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='inventory_movements')
    variant = models.ForeignKey(ProductVariant, on_delete=models.SET_NULL, null=True, blank=True, related_name='inventory_movements')
    movement_type = models.CharField(max_length=32, choices=MOVEMENT_TYPE_CHOICES)
    quantity = models.IntegerField()
    reason = models.CharField(max_length=255, blank=True)
    related_order = models.ForeignKey('Order', on_delete=models.SET_NULL, null=True, blank=True, related_name='inventory_movements')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='bazaar_inventory_movements')
    created_at = models.DateTimeField(auto_now_add=True)


class Cart(DomainEntity):
    STATUS_CHOICES = [('active','Active'),('converted','Converted'),('abandoned','Abandoned'),('expired','Expired')]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name='bazaar_carts')
    session_key = models.CharField(max_length=128, blank=True)
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='carts')
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='active')
    currency = models.CharField(max_length=3, default='PLN')
    coupon = models.ForeignKey('Coupon', on_delete=models.SET_NULL, null=True, blank=True, related_name='carts')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True, blank=True)


class CartItem(DomainEntity):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='cart_items')
    variant = models.ForeignKey(ProductVariant, on_delete=models.SET_NULL, null=True, blank=True, related_name='cart_items')
    quantity = models.PositiveIntegerField(default=1)
    unit_price_snapshot = models.DecimalField(max_digits=12, decimal_places=2)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    @property
    def line_total(self): return self.unit_price_snapshot * self.quantity


class Order(DomainEntity):
    STATUS_CHOICES = [('draft','Draft'),('placed','Placed'),('confirmed','Confirmed'),('processing','Processing'),('completed','Completed'),('cancelled','Cancelled'),('refunded','Refunded'),('failed','Failed')]
    PAYMENT_STATUS_CHOICES = [('unpaid','Unpaid'),('pending','Pending'),('authorized','Authorized'),('paid','Paid'),('partially_refunded','Partially refunded'),('refunded','Refunded'),('failed','Failed')]
    FULFILLMENT_STATUS_CHOICES = [('unfulfilled','Unfulfilled'),('partial','Partial'),('fulfilled','Fulfilled'),('delivered','Delivered'),('returned','Returned'),('not_required','Not required')]
    order_number = models.CharField(max_length=64, unique=True, blank=True)
    shop = models.ForeignKey(Shop, on_delete=models.PROTECT, related_name='orders')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='bazaar_orders')
    customer_profile = models.ForeignKey('people.Person', on_delete=models.SET_NULL, null=True, blank=True, related_name='bazaar_orders')
    email = models.EmailField()
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='placed')
    payment_status = models.CharField(max_length=32, choices=PAYMENT_STATUS_CHOICES, default='unpaid')
    fulfillment_status = models.CharField(max_length=32, choices=FULFILLMENT_STATUS_CHOICES, default='unfulfilled')
    currency = models.CharField(max_length=3, default='PLN')
    subtotal_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    shipping_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    billing_address = models.ForeignKey('locations.Address', on_delete=models.SET_NULL, null=True, blank=True, related_name='billing_orders')
    shipping_address = models.ForeignKey('locations.Address', on_delete=models.SET_NULL, null=True, blank=True, related_name='shipping_orders')
    billing_address_snapshot = models.JSONField(default=dict, blank=True)
    shipping_address_snapshot = models.JSONField(default=dict, blank=True)
    customer_note = models.TextField(blank=True)
    internal_note = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ledger_account = models.ForeignKey('assets.LedgerAccount', on_delete=models.SET_NULL, null=True, blank=True, related_name='bazaar_orders')
    ledger_asset = models.ForeignKey('assets.Asset', on_delete=models.SET_NULL, null=True, blank=True, related_name='bazaar_orders')
    ledger_tx_reference = models.CharField(max_length=255, blank=True)
    payment_method = models.CharField(max_length=32, blank=True, default='', choices=[('transfer','Transfer'),('on_delivery','On delivery'),('bank_transfer','Bank transfer'),('manual','Manual')])
    obligation = models.ForeignKey('assets.Obligation', on_delete=models.SET_NULL, null=True, blank=True, related_name='bazaar_orders')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    class Meta: ordering = ['-created_at']
    def __str__(self): return self.order_number or f'Order {self.pk}'
    def save(self, *args, **kwargs):
        new = not self.pk
        if not self.order_number:
            self.order_number = f'BZR-{timezone.now().strftime("%Y%m%d%H%M%S")}-NEW'
        super().save(*args, **kwargs)
        if new and self.order_number.endswith('-NEW'):
            self.order_number = f'BZR-{timezone.now().strftime("%Y%m%d")}-{self.pk}'
            super().save(update_fields=['order_number'])


class OrderItem(DomainEntity):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name='order_items')
    variant = models.ForeignKey(ProductVariant, on_delete=models.SET_NULL, null=True, blank=True, related_name='order_items')
    vendor = models.ForeignKey(Vendor, on_delete=models.SET_NULL, null=True, blank=True, related_name='order_items')
    product_name_snapshot = models.CharField(max_length=255)
    sku_snapshot = models.CharField(max_length=128, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    total_price = models.DecimalField(max_digits=12, decimal_places=2)
    metadata = models.JSONField(default=dict, blank=True)


class ServiceDelivery(DomainEntity):
    """
    Tracks receiver confirmation for service-type order items.
    Payment or fulfillment is only released after the receiver (and optionally a
    custodian) confirms the work was delivered.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending confirmation'),
        ('confirmed', 'Confirmed by receiver'),
        ('disputed', 'Disputed'),
        ('custodian_review', 'Under custodian review'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    order_item = models.OneToOneField(OrderItem, on_delete=models.CASCADE, related_name='service_delivery')
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default='pending')
    receiver_note = models.TextField(blank=True)
    dispute_note = models.TextField(blank=True)
    custodian = models.ForeignKey(
        'MarketCustodian', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='service_deliveries',
    )
    custodian_approval_status = models.CharField(
        max_length=16,
        choices=[('not_required', 'Not required'), ('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')],
        default='not_required',
    )
    custodian_note = models.TextField(blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'service delivery'
        verbose_name_plural = 'service deliveries'

    def __str__(self):
        return f'ServiceDelivery for {self.order_item}'


class OrderStatusEvent(DomainEntity):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='status_events')
    previous_status = models.CharField(max_length=32, blank=True)
    new_status = models.CharField(max_length=32)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='bazaar_order_status_events')
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ['-created_at']


class PaymentIntent(DomainEntity):
    PROVIDER_CHOICES = [('stripe','Stripe'),('paypal','PayPal'),('bank_transfer','Bank transfer'),('cash_on_delivery','Cash on delivery'),('manual','Manual'),('algorand','Algorand'),('internal_credit','Internal credit')]
    STATUS_CHOICES = [('created','Created'),('requires_action','Requires action'),('processing','Processing'),('succeeded','Succeeded'),('failed','Failed'),('cancelled','Cancelled'),('refunded','Refunded')]
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='payment_intents')
    provider = models.CharField(max_length=32, choices=PROVIDER_CHOICES)
    provider_reference = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='created')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default='PLN')
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class PaymentTransaction(DomainEntity):
    payment_intent = models.ForeignKey(PaymentIntent, on_delete=models.CASCADE, related_name='transactions')
    transaction_type = models.CharField(max_length=32)
    provider_reference = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    raw_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class ShippingMethod(DomainEntity):
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='shipping_methods')
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    is_active = models.BooleanField(default=True)
    countries = models.JSONField(default=list, blank=True)
    zones = models.ManyToManyField('locations.Zone', blank=True, related_name='bazaar_shipping_methods')
    pickup_address = models.ForeignKey('locations.Address', on_delete=models.SET_NULL, null=True, blank=True, related_name='bazaar_shipping_methods')
    metadata = models.JSONField(default=dict, blank=True)


class Shipment(DomainEntity):
    STATUS_CHOICES = [('pending','Pending'),('packed','Packed'),('shipped','Shipped'),('delivered','Delivered'),('failed','Failed'),('returned','Returned')]
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='shipments')
    vendor = models.ForeignKey(Vendor, on_delete=models.SET_NULL, null=True, blank=True, related_name='shipments')
    shipping_method = models.ForeignKey(ShippingMethod, on_delete=models.SET_NULL, null=True, blank=True, related_name='shipments')
    origin_address = models.ForeignKey('locations.Address', on_delete=models.SET_NULL, null=True, blank=True, related_name='origin_shipments')
    destination_address = models.ForeignKey('locations.Address', on_delete=models.SET_NULL, null=True, blank=True, related_name='destination_shipments')
    tracking_number = models.CharField(max_length=255, blank=True)
    carrier = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='pending')
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)


class Coupon(DomainEntity):
    DISCOUNT_TYPE_CHOICES = [('percentage','Percentage'),('fixed_amount','Fixed amount'),('free_shipping','Free shipping')]
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='coupons')
    code = models.CharField(max_length=64)
    discount_type = models.CharField(max_length=32, choices=DISCOUNT_TYPE_CHOICES)
    value = models.DecimalField(max_digits=12, decimal_places=2)
    usage_limit = models.PositiveIntegerField(null=True, blank=True)
    used_count = models.PositiveIntegerField(default=0)
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_until = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    class Meta: unique_together = [('shop', 'code')]


class OrderDiscount(DomainEntity):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='discounts')
    coupon = models.ForeignKey(Coupon, on_delete=models.SET_NULL, null=True, blank=True, related_name='order_discounts')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=255, blank=True)


class ProductReview(DomainEntity):
    STATUS_CHOICES = [('pending','Pending'),('published','Published'),('rejected','Rejected'),('hidden','Hidden')]
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='reviews')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='bazaar_product_reviews')
    person = models.ForeignKey('people.Person', on_delete=models.SET_NULL, null=True, blank=True, related_name='bazaar_product_reviews')
    order_item = models.ForeignKey(OrderItem, on_delete=models.SET_NULL, null=True, blank=True, related_name='reviews')
    rating = models.PositiveSmallIntegerField()
    title = models.CharField(max_length=255, blank=True)
    body = models.TextField(blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ['-created_at']


class VendorReview(DomainEntity):
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='reviews')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='bazaar_vendor_reviews')
    person = models.ForeignKey('people.Person', on_delete=models.SET_NULL, null=True, blank=True, related_name='bazaar_vendor_reviews')
    order = models.ForeignKey(Order, on_delete=models.SET_NULL, null=True, blank=True, related_name='vendor_reviews')
    rating = models.PositiveSmallIntegerField()
    body = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Wishlist(DomainEntity):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='bazaar_wishlists')
    person = models.ForeignKey('people.Person', on_delete=models.SET_NULL, null=True, blank=True, related_name='bazaar_wishlists')
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='wishlists')
    name = models.CharField(max_length=255, default='Wishlist')
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class WishlistItem(DomainEntity):
    wishlist = models.ForeignKey(Wishlist, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='wishlist_items')
    variant = models.ForeignKey(ProductVariant, on_delete=models.SET_NULL, null=True, blank=True, related_name='wishlist_items')
    added_at = models.DateTimeField(auto_now_add=True)
    class Meta: unique_together = [('wishlist', 'product', 'variant')]


class ShipmentLocation(DomainEntity):
    """A user-saved delivery address for quick reuse at checkout."""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='shipment_locations',
    )
    address = models.ForeignKey(
        'locations.Address',
        on_delete=models.CASCADE,
        related_name='shipment_locations',
    )
    label = models.CharField(max_length=100, blank=True, help_text="e.g. Home, Work, Mum's")
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-is_default', '-created_at']

    def __str__(self):
        name = self.label or self.address.locality_name or str(self.address)
        return f"{name} ({self.user})"

    def save(self, *args, **kwargs):
        if self.is_default:
            ShipmentLocation.objects.filter(user=self.user, is_default=True).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)


class WalletPin(DomainEntity):
    """Per-user wallet PIN stored as a Gervazy EncryptedSecret."""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='wallet_pin',
    )
    secret = models.OneToOneField(
        'gervazy.EncryptedSecret',
        on_delete=models.CASCADE,
        related_name='wallet_pin',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"WalletPin({self.user})"


# ---------------------------------------------------------------------------
# Market custodian
# ---------------------------------------------------------------------------

class MarketCustodian(DomainEntity):
    """
    An entity authorised to regulate and approve specific products or product types
    in a shop.  Regulated products require custodian approval before they can be
    published or have orders confirmed.
    """
    SCOPE_CHOICES = [
        ('all', 'All products'),
        ('type', 'Specific product types'),
        ('category', 'Specific categories'),
    ]

    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='custodians')
    person = models.ForeignKey(
        'people.Person', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='market_custodianships',
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    scope = models.CharField(max_length=16, choices=SCOPE_CHOICES, default='all')
    regulated_product_types = models.JSONField(
        default=list, blank=True,
        help_text='List of product_type values this custodian regulates (used when scope=type).',
    )
    regulated_categories = models.ManyToManyField(
        'ProductCategory', blank=True,
        related_name='custodians',
        help_text='Categories this custodian regulates (used when scope=category).',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.shop})'
