from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
import requests


class SyncService:
    def __init__(self, api_url: str, api_key: str, apps_to_sync=None):
        """
        api_url      → full base URL of remote sync endpoint (e.g. https://x.com/sync/)
        api_key      → decrypted API key (string)
        apps_to_sync → optional override; otherwise read from settings
        """

        self.api_url = api_url.rstrip("/") + "/"   # normalize
        self.api_key = api_key
        self.apps_to_sync = apps_to_sync

        if not self.apps_to_sync:
            raise ImproperlyConfigured("APPS_TO_SYNC must be provided or defined in settings.")

        if not self.api_url:
            raise ImproperlyConfigured("SyncService requires an API URL.")

        if not self.api_key:
            raise ImproperlyConfigured("SyncService requires an API key.")

    # ---------------------------------------------------------
    # PUBLIC API
    # ---------------------------------------------------------

    def sync_all(self):
        for app_label in self.apps_to_sync:
            self.sync_app(app_label)

    def sync_app(self, app_label):
        models = apps.get_app_config(app_label).get_models()
        for model in models:
            self.sync_model(model)

    def sync_model(self, model):
        data = self.pull_remote(model)
        self.apply_sync(model, data)

    # ---------------------------------------------------------
    # SYNC LOGIC
    # ---------------------------------------------------------

    def pull_remote(self, model):
        url = f"{self.api_url}{model._meta.app_label}/{model._meta.model_name}/"
        headers = {"Authorization": f"Token {self.api_key}"}

        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()

    def apply_sync(self, model, data):
        model.objects.all().delete()
        objs = [model(**item) for item in data]
        model.objects.bulk_create(objs)
        print(f"Synced {model._meta.label} ({len(objs)} records)")
