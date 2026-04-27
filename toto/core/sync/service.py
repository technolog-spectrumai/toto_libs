import base64
import requests
from datetime import datetime, timezone
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding


class SyncService:
    def __init__(self, api_url: str, platform, apps_to_sync=None):
        """
        api_url      → base sync URL (e.g. https://x.com/sync/)
        platform     → Platform instance (must have api_keypair)
        apps_to_sync → list of apps to sync
        """

        self.api_url = api_url.rstrip("/") + "/"
        self.platform = platform
        self.apps_to_sync = apps_to_sync

        if not self.apps_to_sync:
            raise ImproperlyConfigured("APPS_TO_SYNC must be provided.")

        if not self.platform.api_keypair:
            raise ImproperlyConfigured("Platform has no RSA keypair assigned.")

    # ---------------------------------------------------------
    # INTERNAL: Build RSA headers
    # ---------------------------------------------------------
    def _headers(self):
        timestamp = datetime.now(timezone.utc).isoformat()

        private_key = self.platform.api_keypair.get_private_key()

        signature = private_key.sign(
            timestamp.encode(),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )

        return {
            "Authorization": "Signature " + base64.b64encode(signature).decode(),
            "X-Platform-ID": self.platform.api_keypair.key_id,
            "X-Timestamp": timestamp,
        }

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
        response = requests.get(url, headers=self._headers())
        response.raise_for_status()
        return response.json()

    def apply_sync(self, model, data):
        model.objects.all().delete()
        objs = [model(**item) for item in data]
        model.objects.bulk_create(objs)
        print(f"Synced {model._meta.label} ({len(objs)} records)")
