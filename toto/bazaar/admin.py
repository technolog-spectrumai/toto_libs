from django.contrib import admin
from .models import *

class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1

class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1

@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'currency', 'is_active', 'owner')
    prepopulated_fields = {'slug': ('name',)}

@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'shop', 'status', 'verified_at')
    list_filter = ('status', 'shop')
    prepopulated_fields = {'slug': ('display_name',)}
    filter_horizontal = ('accepted_currencies',)

@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'shop', 'parent', 'position', 'is_active')
    list_filter = ('shop', 'is_active')
    prepopulated_fields = {'slug': ('name',)}

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'shop', 'vendor', 'product_type', 'status', 'price', 'stock_quantity', 'is_public')
    list_filter = ('shop', 'vendor', 'product_type', 'status', 'is_public', 'is_featured')
    search_fields = ('name', 'summary', 'description')
    prepopulated_fields = {'slug': ('name',)}
    inlines = [ProductImageInline, ProductVariantInline]

@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ('id', 'shop', 'user', 'status', 'currency', 'updated_at')

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('order_number', 'shop', 'email', 'status', 'payment_status', 'fulfillment_status', 'total_amount', 'created_at')
    list_filter = ('status', 'payment_status', 'fulfillment_status', 'shop')
    search_fields = ('order_number', 'email')

for model in [CartItem, OrderItem, OrderStatusEvent, PaymentIntent, PaymentTransaction, ShippingMethod, Shipment, Coupon, OrderDiscount, ProductReview, VendorReview, Wishlist, WishlistItem, InventoryMovement]:
    try:
        admin.site.register(model)
    except admin.sites.AlreadyRegistered:
        pass


@admin.register(MarketCustodian)
class MarketCustodianAdmin(admin.ModelAdmin):
    list_display = ('name', 'shop', 'scope', 'is_active', 'person')
    list_filter = ('shop', 'scope', 'is_active')
    search_fields = ('name',)
    filter_horizontal = ('regulated_categories',)


for model in [ServiceDelivery]:
    try:
        admin.site.register(model)
    except admin.sites.AlreadyRegistered:
        pass
