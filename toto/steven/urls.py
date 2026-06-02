from django.urls import path
from . import views

app_name = 'steven'

urlpatterns = [
    path('', views.agent_list, name='agent_list'),
    path('agents/<slug:slug>/', views.agent_detail, name='agent_detail'),
    path('agents/<slug:slug>/quick-ask/', views.quick_ask, name='quick_ask'),
    path('agents/<slug:slug>/estimate-cost/', views.estimate_cost, name='estimate_cost'),
    path('agents/<slug:slug>/chat/', views.conversation_new, name='conversation_new'),
    path('agents/<slug:slug>/chat/<int:pk>/', views.conversation_detail, name='conversation_detail'),
    path('runs/<int:pk>/', views.run_detail, name='run_detail'),
    path('runs/<int:pk>/status/', views.run_status, name='run_status'),
]
