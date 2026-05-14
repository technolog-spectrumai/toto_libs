import requests


class BaseRemoteTransport:
    """
    Base transport for communicating with a RemotePlatform.

    Subclass this per deployment when you need Tor, mTLS, VPN-only routing,
    custom headers, custom CA bundles, special timeout behavior, etc.
    """

    def __init__(self, remote_platform, rule=None):
        self.remote_platform = remote_platform
        self.rule = rule

    @property
    def base_url(self):
        return self.remote_platform.normalized_base_url

    @property
    def timeout(self):
        return self.remote_platform.timeout_seconds

    @property
    def secret_key(self):
        return self.remote_platform.outgoing_secret_key

    def get_headers(self):
        return {
            "Authorization": f"Bearer {self.secret_key}",
            "Accept": "application/json",
        }

    def get_request_kwargs(self):
        return {
            "verify": True,
        }

    def upload_package(self, package_path):
        with open(package_path, "rb") as f:
            response = requests.post(
                f"{self.base_url}/api/sync/import/",
                headers=self.get_headers(),
                files={
                    "package": ("sync.zip", f, "application/zip"),
                },
                timeout=self.timeout,
                **self.get_request_kwargs(),
            )

        response.raise_for_status()
        return response.json()

    def list_remote_objects(self, model_label):
        response = requests.get(
            f"{self.base_url}/api/sync/models/{model_label}/objects/",
            headers=self.get_headers(),
            timeout=self.timeout,
            **self.get_request_kwargs(),
        )

        response.raise_for_status()
        return response.json()

    def get_remote_object(self, model_label, uid):
        response = requests.get(
            f"{self.base_url}/api/sync/models/{model_label}/objects/{uid}/",
            headers=self.get_headers(),
            timeout=self.timeout,
            **self.get_request_kwargs(),
        )

        response.raise_for_status()
        return response.json()


class RequestsTransport(BaseRemoteTransport):
    """
    Default normal HTTPS/HTTP transport.
    """
    pass


class TorTransport(BaseRemoteTransport):
    """
    Generic Tor transport.

    Requires:
        pip install "requests[socks]"

    Uses socks5h so DNS/.onion resolution happens through Tor.
    """

    tor_proxy_url = "socks5h://127.0.0.1:9050"

    def get_request_kwargs(self):
        return {
            "verify": False,
            "proxies": {
                "http": self.tor_proxy_url,
                "https": self.tor_proxy_url,
            },
        }


class TorBrowserTransport(TorTransport):
    """
    Tor Browser usually exposes SOCKS on port 9150.
    """

    tor_proxy_url = "socks5h://127.0.0.1:9150"
