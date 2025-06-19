# -*- coding: utf-8 -*-
from . import BaseDatabase
from meilisearch_python_sdk import AsyncClient
from meilisearch_python_sdk.errors import MeilisearchApiError
from meilisearch_python_sdk.models.settings import (
    MeilisearchSettings,
    ProximityPrecision,
)
from typing import Any, Dict, List, Set
import asyncio
from loguru import logger


class MeilisearchDriver(BaseDatabase):
    """Meilisearch database driver."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.debug = config.get("debug", False)
        self.client = AsyncClient(
            url=self.config.get("url", "http://127.0.0.1:7700"),
            api_key=self.config.get("api_key") or None,
        )
        self._indexes_created: Set[str] = set()
        self._index_locks: Dict[str, asyncio.Lock] = {}

    async def connect(self) -> None:
        """Checks the connection to the Meilisearch instance."""
        try:
            health = await self.client.health()
            if health.status != "available":
                raise ConnectionError("Meilisearch is not available.")
            logger.info(
                f"Meilisearch connection established at {self.config.get('url')}."
            )
        except Exception as e:
            logger.opt(exception=True).error(
                f"Failed to connect to Meilisearch"
            )
            logger.debug(str(e))
            raise

    async def close(self) -> None:
        """Closes the Meilisearch client session."""
        await self.client.aclose()
        logger.info("Meilisearch client session closed.")

    async def _ensure_monthly_index(self, index_name: str) -> None:
        """Ensures an index exists and is configured, using a lock to prevent race conditions."""
        # Get or create a lock for the specific index name
        lock = self._index_locks.setdefault(index_name, asyncio.Lock())

        async with lock:
            if index_name in self._indexes_created:
                return

            logger.debug(
                f"Ensuring index '{index_name}' exists and is configured..."
            )

            # This just sends the task to Meilisearch. We don't need the return value.
            try:
                await self.client.create_index(uid=index_name, primary_key="id")
                logger.debug(f"Index creation task for '{index_name}' sent.")
            except MeilisearchApiError as e:
                # It's fine if the index already exists. We'll configure it next.
                if e.code != "index_already_exists":
                    raise  # Re-raise other unexpected API errors

            # Now, configure the index. The wait on this task will ensure the index
            # is fully created and ready before we proceed.
            index = self.client.index(index_name)
            settings = MeilisearchSettings(
                searchable_attributes=[
                    "Message",
                ],
                filterable_attributes=[
                    "ReceivedAt",
                    "DeviceReportedTime",
                    "Priority",
                    "Facility",
                    "FromHost",
                    "SysLogTag",
                    "ProcessID",
                ],
                sortable_attributes=["ReceivedAt", "DeviceReportedTime"],
                proximity_precision=ProximityPrecision.BY_ATTRIBUTE,
            )

            settings_task = await index.update_settings(settings)
            await self.client.wait_for_task(settings_task.task_uid)

            self._indexes_created.add(index_name)
            logger.debug(f"Index '{index_name}' is ready.")

    async def write_batch(self, batch: List[Dict[str, Any]]) -> None:
        """Writes a batch of log documents to Meilisearch."""
        if not batch:
            return

        batches_by_index: Dict[str, List[Dict[str, Any]]] = {}
        for i, msg in enumerate(batch):
            index_name = msg["ReceivedAt"].strftime("SystemEvents%Y%m")
            if index_name not in batches_by_index:
                batches_by_index[index_name] = []

            # Meilisearch needs a unique ID for each document.
            # ID must be alphanumeric, no periods allowed.
            doc = msg.copy()
            doc_id_ts = str(msg["ReceivedAt"].timestamp()).replace(".", "")
            doc["id"] = f"{doc_id_ts}-{i}"

            # Convert datetime objects to strings for JSON serialization
            doc["ReceivedAt"] = msg["ReceivedAt"].isoformat()
            doc["DeviceReportedTime"] = msg["DeviceReportedTime"].isoformat()

            batches_by_index[index_name].append(doc)

        try:
            # Step 1: Send all documents and collect the task uids
            tasks_to_wait: List[int] = []
            for index_name, docs in batches_by_index.items():
                await self._ensure_monthly_index(index_name)
                index = self.client.index(index_name)
                doc_add_task = await index.add_documents(docs)
                tasks_to_wait.append(doc_add_task.task_uid)

            # Step 2: Wait for Meilisearch to confirm all tasks have been processed
            if tasks_to_wait:
                logger.debug(
                    f"Waiting for {len(tasks_to_wait)} Meilisearch task(s) to complete..."
                )

            # Use client.wait_for_task for all collected UIDs.
            wait_tasks = [
                self.client.wait_for_task(uid) for uid in tasks_to_wait
            ]
            completed_tasks = await asyncio.gather(*wait_tasks)

            # Step 3: Check if any tasks failed.
            for task_result in completed_tasks:
                if task_result.status != "succeeded":
                    logger.error(
                        f"Meilisearch task {task_result.uid} failed: {task_result.error}"
                    )

            logger.debug(
                f"Successfully processed batch of {len(batch)} documents in Meilisearch."
            )

        except Exception as e:
            logger.opt(exception=True).error("Error writing to Meilisearch")
            logger.debug(str(e))
