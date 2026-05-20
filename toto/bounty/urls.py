from django.urls import path
from . import views

app_name = 'bounty'

urlpatterns = [
    path('', views.BountyBoardListView.as_view(), name='board-list'),
    path('<slug:board_slug>/', views.BountyListView.as_view(), name='bounty-list'),
    path('<slug:board_slug>/<slug:slug>/', views.BountyDetailView.as_view(), name='bounty-detail'),
    path('<slug:board_slug>/<slug:slug>/claim/', views.BountyClaimCreateView.as_view(), name='bounty-claim'),
    path('my-claims/', views.BountyMyClaimsView.as_view(), name='my-claims'),
    path('claims/<int:claim_pk>/submit/', views.BountySubmissionCreateView.as_view(), name='submit'),
    path('dashboard/', views.BountyDashboardView.as_view(), name='dashboard'),
    path('claims/<int:claim_pk>/action/', views.BountyClaimActionView.as_view(), name='claim-action'),
    path('submissions/<int:submission_pk>/action/', views.BountySubmissionActionView.as_view(), name='submission-action'),
]
