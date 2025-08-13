from django.urls import path
from . import views

urlpatterns = [
    path('pages/<str:language>/', views.page_list, name='page_list'),
    path('pages/<slug:slug>/<str:language>/', views.page_detail, name='page_detail'),
]
