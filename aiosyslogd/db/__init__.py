# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from typing import Any, Dict, List


class BaseDatabase(ABC):
    """Abstract base class for database drivers."""

    @abstractmethod
    async def connect(self) -> None:
        """Connect to the database."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close the database connection."""
        pass

    @abstractmethod
    async def write_batch(self, batch: List[Dict[str, Any]]) -> None:
        """Write a batch of messages to the database."""
        pass
