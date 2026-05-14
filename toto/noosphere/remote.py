from noosphere.services.client import SyncClient


class RemotePreviewClient:
    """
    Live remote preview.

    No cache.
    No import.
    No local persistence.
    """

    def __init__(self, rule, timeout=None):
        self.rule = rule
        self.remote_platform = rule.remote_platform
        self.timeout = timeout or self.remote_platform.timeout_seconds

    def get_client(self):
        client = SyncClient.from_remote_platform(self.remote_platform)
        client.timeout = self.timeout
        return client

    def list_objects(self):
        return self.get_client().list_remote_objects(
            model_label=self.rule.model_label,
        )

    def get_object(self, uid):
        return self.get_client().get_remote_object(
            model_label=self.rule.model_label,
            uid=uid,
        )
