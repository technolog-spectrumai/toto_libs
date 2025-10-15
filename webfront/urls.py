from django.urls import path
from . import views

urlpatterns = [
    path('page/<slug:slug>/<str:lang>/', views.static_page_view, name='static_page'),
    path('image/<slug:slug>/', views.image_view, name='image_url'),
]
