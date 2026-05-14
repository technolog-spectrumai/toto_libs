import requests


class SyncClient:
    """
    HTTP client for remote platform communication.

    Knows only about HTTP.
    Does not know about models, rules, manifests, ZIP internals, or backup services.
    """

    def __init__(
        self,
        base_url,
        secret_key,
        timeout=60,
        verify_ssl=True,
    ):
        if not base_url:
            raise ValueError("base_url is required.")

        if not secret_key:
            raise ValueError("secret_key is required.")

        self.base_url = base_url.rstrip("/")
        self.secret_key = secret_key
        self.timeout = timeout
        self.verify_ssl = verify_ssl

    @classmethod
    def from_remote_platform(cls, remote_platform):
        return cls(
            base_url=remote_platform.normalized_base_url,
            secret_key=remote_platform.outgoing_secret_key,
            timeout=remote_platform.timeout_seconds,
            verify_ssl=remote_platform.verify_ssl,
        )

    def get_headers(self):
        return {
            "Authorization": f"Bearer {self.secret_key}",
            "Accept": "application/json",
        }

    def upload_package(self, package_path):
        """
        Upload signed sync ZIP package to remote instance.
        """

        with open(package_path, "rb") as f:
            response = requests.post(
                f"{self.base_url}/api/sync/import/",
                headers=self.get_headers(),
                files={
                    "package": ("sync.zip", f, "application/zip"),
                },
                timeout=self.timeout,
                verify=self.verify_ssl,
            )

        response.raise_for_status()
        return response.json()

    def list_remote_objects(self, model_label):
        """
        Fetch lightweight remote preview rows.

        Expected remote response:
            [
                {"id": 1, "uid": "...", "name": "Object name"},
                ...
            ]
        """

        response = requests.get(
            f"{self.base_url}/api/sync/models/{model_label}/objects/",
            headers=self.get_headers(),
            timeout=self.timeout,
            verify=self.verify_ssl,
        )

        response.raise_for_status()
        return response.json()

    def get_remote_object(self, model_label, uid):
        """
        Fetch one lightweight remote preview row.
        """

        response = requests.get(
            f"{self.base_url}/api/sync/models/{model_label}/objects/{uid}/",
            headers=self.get_headers(),
            timeout=self.timeout,
            verify=self.verify_ssl,
        )

        response.raise_for_status()
        return response.json()
