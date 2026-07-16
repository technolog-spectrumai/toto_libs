"""
Shared manifest and connection bundle schemas for toto SSO.

ManifestBundle   — what a consumer declares it needs (consumer → provider)
ConnectionBundle — what a provider issues to a consumer (provider → consumer)

Both are plain dataclasses + JSON serialization; no Django dependency.
"""
import json
from dataclasses import dataclass, field, asdict
from typing import List


@dataclass
class OIDCClientSpec:
    name: str
    client_id: str
    client_type: str = "confidential"
    trusted: bool = False
    redirect_uris: List[str] = field(default_factory=list)
    scopes: str = "openid email profile"


@dataclass
class ManifestBundle:
    """Exported by the consumer; imported by the provider to provision a relying party."""
    schema_version: int
    app: str
    display_name: str
    oidc_client: OIDCClientSpec

    def to_dict(self):
        d = asdict(self)
        return d

    def to_json(self, indent=2):
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, d):
        client = OIDCClientSpec(**d["oidc_client"])
        return cls(
            schema_version=d["schema_version"],
            app=d["app"],
            display_name=d["display_name"],
            oidc_client=client,
        )

    @classmethod
    def from_json(cls, s):
        return cls.from_dict(json.loads(s))


@dataclass
class ConnectionBundle:
    """Exported by the provider admin; imported by the consumer admin to configure auth."""
    schema_version: int
    bundle_type: str       # always "oidc_connection_bundle"
    label: str             # human name for this connection, e.g. "Production Portal"
    portal_url: str
    client_id: str
    client_secret: str
    scopes: str = "openid email profile"

    def to_dict(self):
        return asdict(self)

    def to_json(self, indent=2):
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, d):
        return cls(
            schema_version=d["schema_version"],
            bundle_type=d["bundle_type"],
            label=d.get("label", ""),
            portal_url=d["portal_url"],
            client_id=d["client_id"],
            client_secret=d["client_secret"],
            scopes=d.get("scopes", "openid email profile"),
        )

    @classmethod
    def from_json(cls, s):
        return cls.from_dict(json.loads(s))
