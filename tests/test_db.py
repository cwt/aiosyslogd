import pytest
from aiosyslogd.db import BaseDatabase
from typing import Any, Dict, List

# --- Test Suite for BaseDatabase ---


class TestBaseDatabase:
    """Tests for the BaseDatabase abstract base class."""

    def test_cannot_instantiate_base_database(self):
        """Tests that BaseDatabase cannot be instantiated directly."""
        with pytest.raises(TypeError) as exc_info:
            BaseDatabase()
        assert "Can't instantiate abstract class BaseDatabase" in str(
            exc_info.value
        )

    def test_partial_implementation_fails(self):
        """Tests that a partial implementation missing methods cannot be instantiated."""

        class PartialDatabase(BaseDatabase):
            async def connect(self) -> None:
                pass

            async def close(self) -> None:
                pass

            # Missing write_batch

        with pytest.raises(TypeError) as exc_info:
            PartialDatabase()
        assert "Can't instantiate abstract class PartialDatabase" in str(
            exc_info.value
        )
        assert "write_batch" in str(exc_info.value)

    def test_complete_implementation_succeeds(self):
        """Tests that a complete implementation can be instantiated."""

        class CompleteDatabase(BaseDatabase):
            async def connect(self) -> None:
                pass

            async def close(self) -> None:
                pass

            async def write_batch(self, batch: List[Dict[str, Any]]) -> None:
                pass

        # Should not raise any errors
        instance = CompleteDatabase()
        assert isinstance(instance, BaseDatabase)
