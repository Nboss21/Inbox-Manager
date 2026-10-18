"""Pydantic models for the reply agent pipeline."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PriorityEmailContext(BaseModel):
    """Enriched email context fed into the draft-reply agent.

    Fields
    ------
    thread_id:
        Gmail thread ID — used to send the reply in the same thread.
    email_id:
        Gmail message ID — used to mark as reply-processed.
    subject:
        Email subject line.
    sender:
        Full 'From' header value, e.g. "Alice Smith <alice@example.com>".
    sender_email:
        Extracted bare email address, e.g. "alice@example.com".
    snippet:
        Short Gmail snippet (pre-decoded by the API).
    full_body:
        Full decoded body text.  HTML is stripped to readable plain text.
        Empty string if the body could not be fetched.
    received_at:
        Timestamp the message was received (UTC-aware).
    """

    thread_id: str
    email_id: str
    subject: str
    sender: str
    sender_email: str
    snippet: str = Field(default="")
    full_body: str = Field(default="")
    received_at: datetime


class ReplyDecision(BaseModel):
    """Result produced by the reply agent for a single priority email.

    Fields
    ------
    email_id:
        Gmail message ID — links this decision back to the source email.
    needs_reply:
        True when the agent decided the email warrants a draft reply.
    reasoning:
        One or two sentences explaining the decision (always populated,
        even when needs_reply is False — this is the audit trail).
    draft_reply:
        The drafted reply text.  Empty string when needs_reply is False.
    model_used:
        Name of the Gemini model that produced this output.
    """

    email_id: str
    needs_reply: bool
    reasoning: str
    draft_reply: str = Field(default="")
    model_used: str = Field(default="")
