from django.urls import path
from django.apps import apps
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .auth import PlatformSyncAuthentication


class SyncApiManager:
    """
    Auto-registers sync endpoints for all models in selected apps.
    """

    def __init__(self, apps_to_sync):
        self.apps_to_sync = apps_to_sync
        self.urlpatterns = []
        self.build_routes()
        # print("=== SYNC ROUTES ===")
        # for p in self.urlpatterns:
        #     print(" /sync/" + p.pattern._route)
        # print("====================")

    # -------------------------------------------------------
    # STATIC ENDPOINT FACTORY
    # -------------------------------------------------------
    @staticmethod
    def model_list_view(model):
        @api_view(["GET"])
        @authentication_classes([PlatformSyncAuthentication])
        @permission_classes([IsAuthenticated])
        def view(request):
            qs = model.objects.all().values()
            return Response(list(qs))
        return view

    # -------------------------------------------------------
    # ROUTE GENERATOR
    # -------------------------------------------------------
    def build_routes(self):
        for app_label in self.apps_to_sync:
            app_config = apps.get_app_config(app_label)
            for model in app_config.get_models():
                self.urlpatterns.append(
                    path(
                        f"{app_label}/{model._meta.model_name}/",
                        SyncApiManager.model_list_view(model),
                        name=f"sync_{app_label}_{model._meta.model_name}",
                    )
                )
