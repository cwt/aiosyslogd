from abc import ABC, abstractmethod
from typing import Any


class BaseDatabase(ABC):
    """Abstract base class for database drivers."""

    @abstractmethod
    async def connect(self) -> None:
        """Connect to the database."""

    @abstractmethod
    async def close(self) -> None:
        """Close the database connection."""

    @abstractmethod
    async def write_batch(self, batch: list[dict[str, Any]]) -> None:
        """Write a batch of messages to the database."""
