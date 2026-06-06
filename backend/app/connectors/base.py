from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class ConnectorMetadata:
    name: str
    kind: str
    base_url: str
    description: str


@dataclass(slots=True)
class SourceDocument:
    source_name: str
    source_url: str
    captured_at: datetime
    payload: dict[str, Any]


class SourceConnector(ABC):
    """Contract for every external data connector."""

    name: str
    kind: str
    base_url: str
    description: str

    @abstractmethod
    def fetch(self) -> list[SourceDocument]:
        """Return raw source documents from the external provider."""

    def metadata(self) -> ConnectorMetadata:
        return ConnectorMetadata(
            name=self.name,
            kind=self.kind,
            base_url=self.base_url,
            description=self.description,
        )
