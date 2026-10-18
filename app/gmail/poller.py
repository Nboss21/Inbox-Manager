"""Gmail polling helpers and CLI entrypoint."""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from dataclasses import dataclass

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.gmail.client import build_gmail_service
from app.gmail.drafts import create_draft_reply
from app.gmail.labels import apply_label, ensure_labels_exist
from app.classification.classifier import classify_email
from app.gmail.schemas import EmailSummary
from app.replies.agent import get_unhandled_priority_emails, process_priority_email
from app.storage.db import (
    get_email_cursor,
    is_email_processed,
    mark_email_processed,
    mark_reply_processed,
    set_email_cursor,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PollCycleResult:
    new_emails: int = 0
    classified: int = 0
    errors: int = 0


def _received_at_from_message(message: dict) -> str:
    internal_date = message.get("internalDate")
    if not internal_date:
        return datetime.now(timezone.utc).isoformat()
    return datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc).isoformat()


def _cursor_from_received_at(received_at: str) -> str:
    return str(int(datetime.fromisoformat(received_at).timestamp()))


def _message_to_summary(message: dict) -> EmailSummary:
    payload = message.get("payload", {})
    headers = {
        header["name"].lower(): header["value"]
        for header in payload.get("headers", [])
        if "name" in header and "value" in header
    }
    return EmailSummary(
        id=message["id"],
        thread_id=message.get("threadId", message["id"]),
        sender=headers.get("from", ""),
        subject=headers.get("subject", ""),
        snippet=message.get("snippet", ""),
        received_at=_received_at_from_message(message),
        label_ids=message.get("labelIds", []),
    )


@retry(
    retry=retry_if_exception_type(Exception),
    wait=wait_exponential(min=1, max=8),
    stop=stop_after_attempt(3),
)
def fetch_new_emails(since_cursor: str | None) -> list[EmailSummary]:
    """Fetch messages newer than the stored timestamp cursor.

    Timestamp-based polling is used for the scaffold because it is simple to
    bootstrap, easy to store in SQLite, and adequate for a first polling pass.
    Gmail historyId sync is more precise but requires more state management and
    extra recovery handling.
    """

    bundle = build_gmail_service(interactive=False)
    service = bundle.service

    query_parts = ["in:anywhere"]
    if since_cursor:
        query_parts.append(f"after:{since_cursor}")

    response = service.users().messages().list(userId="me", q=" ".join(query_parts), maxResults=50).execute()
    messages = response.get("messages", [])

    summaries: list[EmailSummary] = []
    for message_ref in messages:
        if is_email_processed(message_ref["id"]):
            continue

        message = (
            service.users()
            .messages()
            .get(userId="me", id=message_ref["id"], format="full")
            .execute()
        )
        summaries.append(_message_to_summary(message))

    return summaries


def fetch_new_emails_with_client(since_cursor: str | None, service: object) -> list[EmailSummary]:
    """Fetch new emails using an already-authenticated Gmail service."""

    query_parts = ["in:anywhere"]
    if since_cursor:
        query_parts.append(f"after:{since_cursor}")

    response = service.users().messages().list(userId="me", q=" ".join(query_parts), maxResults=50).execute()
    messages = response.get("messages", [])

    summaries: list[EmailSummary] = []
    for message_ref in messages:
        if is_email_processed(message_ref["id"]):
            continue

        message = (
            service.users()
            .messages()
            .get(userId="me", id=message_ref["id"], format="full")
            .execute()
        )
        summaries.append(_message_to_summary(message))

    return summaries


def poll_once() -> list[EmailSummary]:
    result = run_poll_cycle()
    return result["emails"]


def run_poll_cycle() -> dict[str, object]:
    since_cursor = get_email_cursor()
    bundle = build_gmail_service(interactive=False)
    gmail_client = bundle.service
    ensure_labels_exist(gmail_client)

    emails = fetch_new_emails_with_client(since_cursor, gmail_client)
    latest_timestamp = since_cursor
    result = PollCycleResult(new_emails=len(emails))

    for email in emails:
        try:
            category = classify_email(email)
            label_applied = apply_label(gmail_client, email.id, category)
            mark_email_processed(email.id)
            latest_timestamp = max(latest_timestamp or "0", _cursor_from_received_at(email.received_at))
            result.classified += 1

            logger.info(
                "processed_email",
                extra={
                    "email_id": email.id,
                    "sender": email.sender,
                    "subject": email.subject,
                    "category": category.value,
                    "label_applied": label_applied,
                },
            )
        except Exception:
            result.errors += 1
            logger.exception(
                "failed_email_processing",
                extra={
                    "email_id": email.id,
                    "sender": email.sender,
                    "subject": email.subject,
                },
            )
            continue

    if latest_timestamp:
        set_email_cursor(latest_timestamp)

    logger.info(
        "Poll cycle complete: %s new emails, %s classified, %s errors.",
        result.new_emails,
        result.classified,
        result.errors,
    )

    # Step 2: Draft replies for unhandled priority emails
    logger.info("Starting reply drafting for unhandled priority emails...")
    try:
        priority_emails = get_unhandled_priority_emails()
        logger.info("Found %d unhandled priority emails to draft replies for.", len(priority_emails))
        for ctx in priority_emails:
            try:
                decision = process_priority_email(ctx)
                if decision.needs_reply:
                    # CRITICAL WARNING: drafts.create only! We must NEVER call drafts.send()
                    create_draft_reply(
                        client=gmail_client,
                        thread_id=ctx.thread_id,
                        to=ctx.sender_email,
                        subject=ctx.subject,
                        body=decision.draft_reply,
                    )
                mark_reply_processed(ctx.email_id)
            except Exception:
                logger.exception(
                    "failed_reply_drafting_for_email",
                    extra={"email_id": ctx.email_id, "subject": ctx.subject},
                )
    except Exception:
        logger.exception("failed_fetching_unhandled_priority_emails")

    return {"emails": emails, "result": result}



def main() -> None:
    parser = argparse.ArgumentParser(description="Poll Gmail once for new emails")
    parser.parse_args()
    poll_once()


if __name__ == "__main__":
    main()
