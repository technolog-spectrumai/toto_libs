from django.urls import path
from toto.socialhub.api_views import (
    ProfileListApiView,
    ProfileDetailApiView,
    CommunityListApiView,
    CommunityDetailApiView,
    CommunityOrgChartApiView,
)
from toto.socialhub.views.profile import (ProfileListView, ProfileDetailView,
                                          search_address, set_location_sharing,
                                          set_my_address, set_preferred_language)
from toto.socialhub.views.community import (
    CommunityListView,
    CommunityDetailView,
    AdministrataView,
    community_org_chart_data_by_slug,
    community_chain_graph_data,
)
from toto.socialhub.views.community_news import (
    community_news_create,
    community_news_delete,
    community_news_update,
)
from toto.socialhub.views.circles import (circle_add, circle_delete, circle_member,
                                          circle_speeds, circles)
from toto.socialhub.views.application import membership_application_view, application_success_view, \
    verification_success_view, reference_request_view, reference_next, verify_application_view, reference_accept, \
    reference_reject

app_name = "socialhub"

urlpatterns = [
    # Enigma JSON API
    path("api/profiles/", ProfileListApiView.as_view(), name="api_profile_list"),
    path("api/profiles/<slug:slug>/", ProfileDetailApiView.as_view(), name="api_profile_detail"),
    path("api/communities/", CommunityListApiView.as_view(), name="api_community_list"),
    path("api/communities/<slug:slug>/", CommunityDetailApiView.as_view(), name="api_community_detail"),
    path("api/communities/<slug:slug>/org-chart/", CommunityOrgChartApiView.as_view(), name="api_community_org_chart"),

    path("profiles/", ProfileListView.as_view(), name="profile_list"),
    path("profiles/<slug:slug>/", ProfileDetailView.as_view(), name="profile_details"),
    path("profiles/language/set/", set_preferred_language, name="set_preferred_language"),
    # Its own door: the one setting whose wrong value publishes where you live.
    path("profiles/address/set/", set_my_address, name="set_my_address"),
    path("profiles/address/search/", search_address, name="search_address"),
    path("profiles/location-sharing/set/", set_location_sharing,
         name="set_location_sharing"),

    path("communities/", CommunityListView.as_view(), name="community_list"),
    # Circles (2026-09-29), for superusers — above the <slug> route, which
    # would otherwise read "circles" as a community's name.
    path("communities/circles/", circles, name="circles"),
    path("communities/circles/add/", circle_add, name="circle_add"),
    path("communities/circles/<int:pk>/members/", circle_member, name="circle_member"),
    path("communities/circles/<int:pk>/speeds/", circle_speeds, name="circle_speeds"),
    path("communities/circles/<int:pk>/delete/", circle_delete, name="circle_delete"),
    # Public: an office nobody can see is not an institution.
    path("communities/<slug:community_slug>/news/new/", community_news_create, name="community_news_create"),
    path("communities/<slug:slug>/", CommunityDetailView.as_view(), name="community_detail"),
    path("communities/<slug:slug>/administrata/", AdministrataView.as_view(), name="administrata"),
    path("communities/<slug:slug>/administrata/graph.json", community_chain_graph_data, name="community_chain_graph_data"),
    path("community-news/<int:pk>/edit/", community_news_update, name="community_news_update"),
    path("community-news/<int:pk>/delete/", community_news_delete, name="community_news_delete"),
    path("community/org-chart/data/<slug:company_slug>/", community_org_chart_data_by_slug,
         name="community_org_chart_data_by_slug"),

    path("apply/membership/", membership_application_view, name="membership_application"),
    path("apply/success/<str:username>/", application_success_view, name="application_success"),
    path("verify/user/<str:username>/", verify_application_view, name="membership_verification"),
    path("verify/success/", verification_success_view, name="application_verified"),
    path("reference/submit/<int:application_id>/", reference_request_view, name="reference_request"),
    path("reference/next/<int:application_id>/", reference_next, name="reference_next"),
    path("reference/<int:ref_id>/accept/", reference_accept, name="reference_accept"),
    path("reference/<int:ref_id>/reject/", reference_reject, name="reference_reject"),
]
