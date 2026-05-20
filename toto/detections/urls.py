from django.urls import path
from . import views

app_name = 'detections'

urlpatterns = [
    # Detections
    path('', views.DetectionListView.as_view(), name='detection-list'),
    path('new/', views.DetectionCreateView.as_view(), name='detection-create'),
    path('<uuid:pk>/', views.DetectionDetailView.as_view(), name='detection-detail'),

    # Bounty boards
    path('boards/', views.BountyBoardListView.as_view(), name='board-list'),
    path('boards/<slug:board_slug>/', views.BountyListView.as_view(), name='bounty-list'),
    path('boards/<slug:board_slug>/<slug:slug>/', views.BountyDetailView.as_view(), name='bounty-detail'),
    path('boards/<slug:board_slug>/<slug:slug>/claim/', views.BountyClaimCreateView.as_view(), name='bounty-claim'),

    # Claims
    path('claims/my/', views.BountyMyClaimsView.as_view(), name='my-claims'),
    path('claims/<int:claim_pk>/submit/', views.BountySubmissionCreateView.as_view(), name='submit'),

    # Management
    path('manage/', views.BountyDashboardView.as_view(), name='dashboard'),
    path('manage/claims/<int:claim_pk>/action/', views.BountyClaimActionView.as_view(), name='claim-action'),
    path('manage/submissions/<int:submission_pk>/action/', views.BountySubmissionActionView.as_view(), name='submission-action'),
    path('manage/payments/<int:payment_pk>/settle/', views.BountyPaymentSettleView.as_view(), name='payment-settle'),
]
