try:
    from rest_framework import serializers
except ImportError:  # DRF optional
    serializers = None

from .models import Product, Vendor, Cart, CartItem, Order, OrderItem, ProductReview

if serializers:
    class VendorSerializer(serializers.ModelSerializer):
        class Meta:
            model = Vendor
            fields = ['id', 'display_name', 'slug', 'bio', 'avatar', 'location', 'website', 'status']

    class ProductSerializer(serializers.ModelSerializer):
        vendor = VendorSerializer(read_only=True)
        class Meta:
            model = Product
            fields = ['id','shop','vendor','category','name','slug','summary','product_type','price','compare_at_price','currency','stock_quantity','is_featured']

    class ProductDetailSerializer(ProductSerializer):
        class Meta(ProductSerializer.Meta):
            fields = ProductSerializer.Meta.fields + ['description', 'metadata', 'created_at', 'updated_at']

    class CartItemSerializer(serializers.ModelSerializer):
        product = ProductSerializer(read_only=True)
        class Meta:
            model = CartItem
            fields = ['id', 'product', 'variant', 'quantity', 'unit_price_snapshot', 'metadata']

    class CartSerializer(serializers.ModelSerializer):
        items = CartItemSerializer(many=True, read_only=True)
        class Meta:
            model = Cart
            fields = ['id', 'shop', 'status', 'currency', 'items', 'updated_at']

    class OrderItemSerializer(serializers.ModelSerializer):
        class Meta:
            model = OrderItem
            fields = ['id', 'product_name_snapshot', 'sku_snapshot', 'quantity', 'unit_price', 'total_price', 'metadata']

    class OrderSerializer(serializers.ModelSerializer):
        items = OrderItemSerializer(many=True, read_only=True)
        class Meta:
            model = Order
            fields = ['id', 'order_number', 'status', 'payment_status', 'fulfillment_status', 'currency', 'total_amount', 'items', 'created_at']

    class ReviewSerializer(serializers.ModelSerializer):
        class Meta:
            model = ProductReview
            fields = ['id', 'product', 'rating', 'title', 'body', 'status', 'created_at']
