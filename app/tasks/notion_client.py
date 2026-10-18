"""Notion client helpers — create incident tasks from Site24x7 alerts."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from notion_client import Client
from notion_client.errors import APIResponseError
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings

if TYPE_CHECKING:
    from app.tasks.models import Site24x7Alert

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------
# Retries on transient Notion API errors and network-level exceptions.
# 3 attempts total: immediate → ~1 s → ~2 s.
# On final failure the exception is re-raised so the caller can log it.
_RETRY_POLICY = dict(
    retry=retry_if_exception_type((APIResponseError, OSError, TimeoutError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)


# ---------------------------------------------------------------------------
# Block builders
# ---------------------------------------------------------------------------

def _heading(text: str) -> dict:
    return {
        "object": "block",
        "type": "heading_2",
        "heading_2": {
            "rich_text": [{"type": "text", "text": {"content": text}}]
        },
    }


def _bullet(label: str, value: str) -> dict:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {
            "rich_text": [
                {
                    "type": "text",
                    "text": {"content": f"{label}: "},
                    "annotations": {"bold": True},
                },
                {"type": "text", "text": {"content": value}},
            ]
        },
    }


def _link_bullet(label: str, url: str) -> dict:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {
            "rich_text": [
                {
                    "type": "text",
                    "text": {"content": f"{label}: "},
                    "annotations": {"bold": True},
                },
                {
                    "type": "text",
                    "text": {"content": url, "link": {"url": url}},
                },
            ]
        },
    }


def _callout(text: str, emoji: str = "🕐") -> dict:
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": [{"type": "text", "text": {"content": text}}],
            "icon": {"type": "emoji", "emoji": emoji},
            "color": "gray_background",
        },
    }


def _build_body_blocks(alert: "Site24x7Alert") -> list[dict]:
    """Return a list of Notion blocks describing the incident."""
    timestamp_str = alert.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")

    blocks: list[dict] = [
        _heading("Incident Details"),
        _bullet("Reason", alert.incident_reason or "—"),
        _link_bullet("Monitor URL", alert.monitor_url or "—"),
        _link_bullet("Dashboard", alert.dashboard_link or "—"),
        _callout(f"Detected at {timestamp_str}", "🕐"),
    ]
    return blocks


# ---------------------------------------------------------------------------
# Property builders
# ---------------------------------------------------------------------------

def _build_properties(alert: "Site24x7Alert") -> dict:
    title = f"[INCIDENT] {alert.monitor_name} is {alert.status}"

    props: dict = {
        # Title property — exists on every Notion database by default
        "Name": {
            "title": [{"type": "text", "text": {"content": title}}]
        },
        # Select property named "Priority" with option "High"
        "Priority": {"select": {"name": "High"}},
        # Status (or Select) property named "Status" with option "To Do"
        "Status": {"status": {"name": "To Do"}},
    }

    # Only set Assignee if a default user ID is configured
    assignee_id = settings.default_assignee_notion_id.strip()
    if assignee_id:
        props["Assignee"] = {"people": [{"object": "user", "id": assignee_id}]}

    return props


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_incident_task(alert: "Site24x7Alert") -> str:
    """Create a Notion page for the given incident alert.

    Returns the URL of the created Notion page.

    Raises:
        APIResponseError: After all retries are exhausted, if Notion rejects
            the request. The caller must handle this and log accordingly.
        OSError / TimeoutError: If a network-level failure persists.
    """
    try:
        return _create_with_retry(alert)
    except Exception:
        logger.error(
            "notion_task_creation_failed",
            extra={
                "monitor_name": alert.monitor_name,
                "status": alert.status,
                "monitor_url": alert.monitor_url,
                "incident_reason": alert.incident_reason,
                "timestamp": alert.timestamp.isoformat(),
            },
            exc_info=True,
        )
        raise


@retry(**_RETRY_POLICY)
def _create_with_retry(alert: "Site24x7Alert") -> str:
    """Inner function that wraps the Notion SDK call with tenacity retry."""
    client = Client(auth=settings.notion_api_key)

    response = client.pages.create(
        parent={"database_id": settings.notion_database_id},
        properties=_build_properties(alert),
        children=_build_body_blocks(alert),
    )

    page_url: str = response.get("url", "")
    logger.info(
        "notion_task_created",
        extra={
            "page_url": page_url,
            "page_id": response.get("id", ""),
            "monitor_name": alert.monitor_name,
            "status": alert.status,
        },
    )
    return page_url
