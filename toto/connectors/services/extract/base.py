"""Extractor plugin contract.

An extractor turns a :class:`~toto.connectors.models.DataConnector`'s
``extract_config`` into a list of raw records plus a per-page audit trail.
v1 ships REST only; a future file-based source registers here the same way.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar


class ExtractError(RuntimeError):
    pass


@dataclass
class FetchPage:
    """One fetched page. ``url`` is composed pre-auth and never carries credentials."""

    url: str
    status_code: int
    json: Any
    record_count: int

    def audit_entry(self):
        """request_log entry — everything except the body (archived separately)."""
        return {
            "url": self.url,
            "status_code": self.status_code,
            "records": self.record_count,
        }


@dataclass
class ExtractResult:
    records: list = field(default_factory=list)
    pages: list = field(default_factory=list)      # list[FetchPage]
    stats: dict = field(default_factory=dict)      # {"pages", "records", "capped"}


class BaseExtractor(ABC):
    kind: ClassVar[str] = ""
    label: ClassVar[str] = ""

    @abstractmethod
    def validate_config(self, config: dict) -> list:
        """Return a list of error strings (empty = valid)."""

    @abstractmethod
    def extract(self, *, data_connector, vault_session=None) -> ExtractResult:
        """Fetch all records for the connector. Raises :class:`ExtractError`."""
