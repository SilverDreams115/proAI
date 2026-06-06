from app.connectors.base import ConnectorMetadata
from app.connectors.base import SourceConnector


class ConnectorRegistry:
    """In-memory connector registry until dynamic loading is added."""

    def __init__(self) -> None:
        self._connectors: dict[str, SourceConnector] = {}

    def register(self, connector: SourceConnector) -> None:
        self._connectors[connector.name] = connector

    def get(self, name: str) -> SourceConnector | None:
        return self._connectors.get(name)

    def list_names(self) -> list[str]:
        return sorted(self._connectors.keys())

    def list_metadata(self) -> list[ConnectorMetadata]:
        return [self._connectors[name].metadata() for name in self.list_names()]

    def clear(self) -> None:
        self._connectors.clear()


connector_registry = ConnectorRegistry()
