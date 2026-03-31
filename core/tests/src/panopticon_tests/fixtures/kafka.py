"""Test helpers for Kafka + aiokafka."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiokafka.admin import AIOKafkaAdminClient


async def await_kafka_controller_ready(
    admin: AIOKafkaAdminClient, *, timeout_s: float = 60.0
) -> None:
    """Wait until cluster metadata includes a controller id.

    ``AIOKafkaAdminClient.create_topics`` sends to the controller; if metadata
    still has ``controller_id == -1`` (or aiokafka parsed API v0), ``_send_to_controller``
    dereferences ``None.nodeId`` and crashes. A short poll loop covers broker warmup.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        await admin._client.force_metadata_update()
        if admin._client.cluster.controller is not None:
            return
        await asyncio.sleep(0.25)
    raise RuntimeError(
        f"Kafka did not report a cluster controller within {timeout_s}s "
        "(broker still warming up or unreachable)."
    )
