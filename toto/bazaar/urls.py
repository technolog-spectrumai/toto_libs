from django.urls import path
from . import views

app_name = 'bazaar'

urlpatterns = [
    path('bazaar/', views.ShopHomeView.as_view(), name='home'),
    path('bazaar/products/', views.ProductListView.as_view(), name='product-list'),
    path('bazaar/products/<slug:slug>/', views.ProductDetailView.as_view(), name='product-detail'),
    path('bazaar/categories/<slug:slug>/', views.CategoryDetailView.as_view(), name='category-detail'),
    path('bazaar/vendors/<slug:slug>/', views.VendorStorefrontView.as_view(), name='vendor-detail'),
    path('bazaar/cart/', views.CartDetailView.as_view(), name='cart'),
    path('bazaar/cart/add/', views.AddToCartView.as_view(), name='cart-add'),
    path('bazaar/cart/item/<int:pk>/update/', views.UpdateCartItemView.as_view(), name='cart-item-update'),
    path('bazaar/checkout/', views.CheckoutView.as_view(), name='checkout'),
    path('bazaar/confirmation/<str:order_number>/', views.OrderConfirmationView.as_view(), name='order-confirmation'),
    path('bazaar/orders/', views.CustomerOrderListView.as_view(), name='customer-orders'),
    path('bazaar/orders/<str:order_number>/', views.CustomerOrderDetailView.as_view(), name='order-detail'),
    path('bazaar/wishlist/', views.WishlistView.as_view(), name='wishlist'),
    path('bazaar/products/<int:product_pk>/review/', views.ReviewCreateView.as_view(), name='review-create'),
    path('bazaar/vendor/', views.VendorDashboardView.as_view(), name='vendor-dashboard'),
    path('bazaar/vendor/products/', views.VendorProductListView.as_view(), name='vendor-products'),
    path('bazaar/vendor/products/new/', views.VendorProductCreateView.as_view(), name='vendor-product-create'),
    path('bazaar/vendor/products/<int:pk>/edit/', views.VendorProductUpdateView.as_view(), name='vendor-product-update'),
    path('bazaar/vendor/orders/', views.VendorOrderListView.as_view(), name='vendor-orders'),
    path('bazaar/vendor/shipments/<int:pk>/edit/', views.VendorShipmentUpdateView.as_view(), name='vendor-shipment-update'),
    path('bazaar/operator/', views.BazaarAdminDashboardView.as_view(), name='operator-dashboard'),
    path('bazaar/operator/products/moderation/', views.ProductModerationQueueView.as_view(), name='operator-product-moderation'),
    path('bazaar/operator/orders/', views.OrderManagementView.as_view(), name='operator-orders'),
]
