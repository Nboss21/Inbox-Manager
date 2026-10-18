"""Draft reply agent helpers."""

from __future__ import annotations

import email.utils
from functools import lru_cache
import logging

from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.classification.categories import CATEGORY_LABELS, Category
from app.config import settings
from app.gmail.client import build_gmail_service, get_full_message
from app.gmail.labels import _find_label_id
from app.replies.models import PriorityEmailContext, ReplyDecision
from app.replies.prompts import (
    REPLY_AGENT_SYSTEM_PROMPT,
    NEEDS_REPLY_PROMPT_TEMPLATE,
    DRAFT_REPLY_PROMPT_TEMPLATE,
)
from app.storage.db import is_reply_processed

logger = logging.getLogger(__name__)

_MODEL_NAME = "gemini-2.5-flash"


class NeedsReplyResponse(BaseModel):
    """Structured response for step 1 of the agent decision."""
    needs_reply: bool = Field(description="Whether the email genuinely needs a reply.")
    reasoning: str = Field(description="One or two sentences explaining why or why not.")


@lru_cache(maxsize=1)
def _genai_client() -> genai.Client:
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    return genai.Client(api_key=settings.gemini_api_key)

# Gmail label name for the PRIORITY category
_PRIORITY_LABEL_NAME = CATEGORY_LABELS[Category.PRIORITY]  # "Inbox Manager/Priority"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_sender(from_header: str) -> tuple[str, str]:
    """Return (display_name_or_email, bare_email) from a 'From' header.

    Examples
    --------
    "Alice Smith <alice@example.com>" → ("Alice Smith", "alice@example.com")
    "bob@example.com"                 → ("bob@example.com", "bob@example.com")
    """
    real_name, addr = email.utils.parseaddr(from_header)
    bare = addr.lower().strip() or from_header.strip()
    display = real_name.strip() or bare
    return display, bare


def _received_at_from_message(message: dict):  # -> datetime
    """Convert Gmail internalDate (ms epoch) to a UTC-aware datetime."""
    from datetime import datetime, timezone

    internal_date = message.get("internalDate")
    if not internal_date:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc)


def _headers_map(message: dict) -> dict[str, str]:
    payload = message.get("payload", {})
    return {
        h["name"].lower(): h["value"]
        for h in payload.get("headers", [])
        if "name" in h and "value" in h
    }


@retry(
    retry=retry_if_exception_type(Exception),
    wait=wait_exponential(min=1, max=8),
    stop=stop_after_attempt(3),
)
def _fetch_priority_messages(service: object, label_id: str) -> list[dict]:
    """List all message refs that carry the Priority label."""
    response = (
        service.users()
        .messages()
        .list(userId="me", labelIds=[label_id], maxResults=50)
        .execute()
    )
    return response.get("messages", [])


@retry(
    retry=retry_if_exception_type(Exception),
    wait=wait_exponential(min=1, max=8),
    stop=stop_after_attempt(3),
)
def _fetch_message_detail(service: object, message_id: str) -> dict:
    return (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_unhandled_priority_emails() -> list[PriorityEmailContext]:
    """Return enriched context for every Priority-labeled email not yet reply-processed.

    Steps
    -----
    1. Authenticate and resolve the 'Inbox Manager/Priority' Gmail label ID.
    2. List messages carrying that label.
    3. Skip any already recorded in the ``reply_processed`` SQLite table.
    4. Fetch the full message (headers + body) for each remaining message.
    5. Return a list of :class:`PriorityEmailContext` — ready for the agent.

    The caller is responsible for calling
    :func:`app.storage.db.mark_reply_processed` once the agent has handled
    each email, so subsequent calls do not re-process it.
    """
    bundle = build_gmail_service(interactive=False)
    service = bundle.service

    # Resolve the Priority label ID
    all_labels_response = service.users().labels().list(userId="me").execute()
    all_labels = all_labels_response.get("labels", [])
    label_id = _find_label_id(all_labels, _PRIORITY_LABEL_NAME)

    if label_id is None:
        logger.warning(
            "priority_label_not_found",
            extra={"label_name": _PRIORITY_LABEL_NAME},
        )
        return []

    message_refs = _fetch_priority_messages(service, label_id)
    contexts: list[PriorityEmailContext] = []

    for ref in message_refs:
        msg_id = ref["id"]

        if is_reply_processed(msg_id):
            logger.debug("reply_already_processed", extra={"email_id": msg_id})
            continue

        try:
            message = _fetch_message_detail(service, msg_id)
            headers = _headers_map(message)

            from_header = headers.get("from", "")
            sender_display, sender_email = _parse_sender(from_header)

            full_body = get_full_message(msg_id, service=service)

            context = PriorityEmailContext(
                thread_id=message.get("threadId", msg_id),
                email_id=msg_id,
                subject=headers.get("subject", "(no subject)"),
                sender=sender_display,
                sender_email=sender_email,
                snippet=message.get("snippet", ""),
                full_body=full_body,
                received_at=_received_at_from_message(message),
            )
            contexts.append(context)

            logger.info(
                "priority_email_prepared",
                extra={
                    "email_id": msg_id,
                    "sender_email": sender_email,
                    "subject": context.subject,
                },
            )
        except Exception:
            logger.exception(
                "priority_email_prep_failed",
                extra={"email_id": msg_id},
            )
            continue

    return contexts


@retry(
    retry=retry_if_exception_type(Exception),
    wait=wait_exponential(min=1, max=8),
    stop=stop_after_attempt(3),
)
def process_priority_email(ctx: PriorityEmailContext) -> ReplyDecision:
    """Analyse a priority email to decide if it needs a reply, and draft one if so.

    Parameters
    ----------
    ctx:
        The PriorityEmailContext structure containing headers and full body text.

    Returns
    -------
    ReplyDecision
        A structure linking the decision, reasoning, and optional draft reply.
    """
    client = _genai_client()

    # Step 1: Decide if reply is needed
    needs_reply_prompt = NEEDS_REPLY_PROMPT_TEMPLATE.format(
        sender=ctx.sender,
        sender_email=ctx.sender_email,
        subject=ctx.subject,
        received_at=ctx.received_at.isoformat(),
        body=ctx.full_body or ctx.snippet or "(empty body)",
    )

    decision_resp = client.models.generate_content(
        model=_MODEL_NAME,
        contents=needs_reply_prompt,
        config=types.GenerateContentConfig(
            system_instruction=REPLY_AGENT_SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=NeedsReplyResponse,
            temperature=0.0,
        ),
    )

    parsed = getattr(decision_resp, "parsed", None)
    if isinstance(parsed, NeedsReplyResponse):
        decision = parsed
    else:
        text = getattr(decision_resp, "text", "")
        if text:
            decision = NeedsReplyResponse.model_validate_json(text)
        else:
            raise RuntimeError("Gemini returned an empty response for reply decision step")

    # Audit logging for reasoning (whether reply is needed or not)
    logger.info(
        "reply_agent_decision",
        extra={
            "email_id": ctx.email_id,
            "needs_reply": decision.needs_reply,
            "reasoning": decision.reasoning,
            "subject": ctx.subject,
            "sender_email": ctx.sender_email,
        },
    )

    draft_reply = ""
    # Step 2: Generate draft if needed
    if decision.needs_reply:
        draft_prompt = DRAFT_REPLY_PROMPT_TEMPLATE.format(
            sender=ctx.sender,
            sender_email=ctx.sender_email,
            subject=ctx.subject,
            received_at=ctx.received_at.isoformat(),
            body=ctx.full_body or ctx.snippet or "(empty body)",
        )

        draft_resp = client.models.generate_content(
            model=_MODEL_NAME,
            contents=draft_prompt,
            config=types.GenerateContentConfig(
                system_instruction=REPLY_AGENT_SYSTEM_PROMPT,
                temperature=0.2,
            ),
        )
        draft_reply = draft_resp.text or ""

    return ReplyDecision(
        email_id=ctx.email_id,
        needs_reply=decision.needs_reply,
        reasoning=decision.reasoning,
        draft_reply=draft_reply.strip(),
        model_used=_MODEL_NAME,
    )

