"""
OpenSearch helpers shared by log retention and client erasure.

Graylog rotates its indices (graylog_0, graylog_1, ...) and locks rotated
ones read-only (index.blocks.write). Deleting documents from them requires
lifting that lock just long enough, then restoring it so Graylog's own
bookkeeping stays consistent.
"""

import logging

import httpx

from app.config import settings

log = logging.getLogger(__name__)

HISTORY_INDEX = "bloodhound_client_history"


async def graylog_indices(client: httpx.AsyncClient) -> list[str]:
    """All current graylog_* indices — never a hardcoded list."""
    r = await client.get(f"{settings.opensearch_url}/_cat/indices/graylog_*?h=index")
    r.raise_for_status()
    return [i.strip() for i in r.text.splitlines() if i.strip()]


async def _is_write_locked(client: httpx.AsyncClient, index: str) -> bool:
    r = await client.get(f"{settings.opensearch_url}/{index}/_settings")
    try:
        blocks = r.json().get(index, {}).get("settings", {}).get("index", {}).get("blocks", {})
        return str(blocks.get("write", "false")).lower() == "true"
    except Exception:
        return False


async def _set_write_lock(client: httpx.AsyncClient, index: str, locked: bool) -> None:
    await client.put(
        f"{settings.opensearch_url}/{index}/_settings",
        json={"index.blocks.write": locked},
    )


async def delete_by_query(client: httpx.AsyncClient, index: str, query: dict) -> int:
    """
    Delete matching documents from one index, handling Graylog's write lock.
    Returns the number of deleted documents. Failures are logged, not
    swallowed silently, and the lock is always restored.
    """
    was_locked = await _is_write_locked(client, index)
    if was_locked:
        await _set_write_lock(client, index, False)
    try:
        r = await client.post(
            f"{settings.opensearch_url}/{index}/_delete_by_query"
            "?conflicts=proceed&refresh=true&wait_for_completion=true",
            json=query,
        )
        if r.status_code != 200:
            log.error(f"delete_by_query on {index} failed: HTTP {r.status_code} {r.text[:500]}")
            return 0
        return r.json().get("deleted", 0)
    finally:
        if was_locked:
            await _set_write_lock(client, index, True)
