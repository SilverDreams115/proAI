from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from typing import Any


class SourceParser(ABC):
    profile_name: str

    @abstractmethod
    def parse(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Normalize a raw connector payload into the internal parsing contract."""
