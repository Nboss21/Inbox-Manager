"""Frontend-facing REST API routes for the Inbox Manager dashboard."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from pydantic import BaseModel

from app.config import settings
from app.gmail.poller import run_poll_cycle
from app.storage.db import get_connection, initialize_database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["dashboard"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class DashboardStats(BaseModel):
    total_processed: int
    total_reply_processed: int
    cursor_value: str | None
    cursor_updated_at: str | None
    poll_interval_minutes: int
    app_name: str
    app_env: str


class ProcessedEmail(BaseModel):
    email_id: str
    processed_at: str


class ReplyProcessed(BaseModel):
    email_id: str
    processed_at: str


class PollTriggerResponse(BaseModel):
    status: str
    message: str
    triggered_at: str


class RecentActivity(BaseModel):
    emails: list[ProcessedEmail]
    replies: list[ReplyProcessed]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_stats_from_db() -> dict[str, Any]:
    initialize_database()
    with get_connection() as conn:
        total_processed = conn.execute(
            "SELECT COUNT(*) as cnt FROM processed_emails"
        ).fetchone()["cnt"]

        total_replies = conn.execute(
            "SELECT COUNT(*) as cnt FROM reply_processed"
        ).fetchone()["cnt"]

        cursor_row = conn.execute(
            "SELECT cursor_value, updated_at FROM email_cursor WHERE id = 1"
        ).fetchone()

    return {
        "total_processed": total_processed,
        "total_reply_processed": total_replies,
        "cursor_value": cursor_row["cursor_value"] if cursor_row else None,
        "cursor_updated_at": cursor_row["updated_at"] if cursor_row else None,
    }


def _get_recent_emails(limit: int = 50) -> list[dict]:
    initialize_database()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT email_id, processed_at FROM processed_emails ORDER BY processed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def _get_recent_replies(limit: int = 50) -> list[dict]:
    initialize_database()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT email_id, processed_at FROM reply_processed ORDER BY processed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=DashboardStats)
def get_dashboard_stats() -> DashboardStats:
    """Return aggregate stats for the dashboard overview cards."""
    db_stats = _get_stats_from_db()
    return DashboardStats(
        **db_stats,
        poll_interval_minutes=settings.poll_interval_minutes,
        app_name=settings.app_name,
        app_env=settings.app_env,
    )


@router.get("/activity", response_model=RecentActivity)
def get_recent_activity(limit: int = 50) -> RecentActivity:
    """Return the most recent processed emails and reply drafts."""
    return RecentActivity(
        emails=[ProcessedEmail(**e) for e in _get_recent_emails(limit)],
        replies=[ReplyProcessed(**r) for r in _get_recent_replies(limit)],
    )


@router.post("/poll", response_model=PollTriggerResponse)
def trigger_poll(background_tasks: BackgroundTasks) -> PollTriggerResponse:
    """Manually trigger a Gmail poll cycle in the background."""
    background_tasks.add_task(_run_poll_background)
    return PollTriggerResponse(
        status="accepted",
        message="Poll cycle triggered. Gmail will be scanned for new emails.",
        triggered_at=datetime.now(timezone.utc).isoformat(),
    )


def _run_poll_background() -> None:
    try:
        result = run_poll_cycle()
        logger.info(
            "manual_poll_complete",
            extra={"result": str(result.get("result"))},
        )
    except Exception:
        logger.exception("manual_poll_failed")


@router.get("/health")
def api_health() -> dict[str, str]:
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}
