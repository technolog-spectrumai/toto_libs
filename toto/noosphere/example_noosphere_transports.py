"""
Example deployment-specific transports.

Put this into your project, for example:

    portal/noosphere_transports.py

Then configure:

    NOOSPHERE_TRANSPORTS = {
        "studio_https": "portal.noosphere_transports.StudioHttpsTransport",
        "studio_tor": "portal.noosphere_transports.StudioTorTransport",
    }
"""

from toto.noosphere.transports import BaseRemoteTransport


class StudioHttpsTransport(BaseRemoteTransport):
    def get_request_kwargs(self):
        return {
            "verify": True,
        }


class StudioTorTransport(BaseRemoteTransport):
    def get_request_kwargs(self):
        return {
            "verify": False,
            "proxies": {
                "http": "socks5h://127.0.0.1:9050",
                "https": "socks5h://127.0.0.1:9050",
            },
        }
