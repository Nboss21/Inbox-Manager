"""Gmail draft creation helpers."""

from __future__ import annotations

import base64
import logging
from email.message import EmailMessage

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


@retry(
    retry=retry_if_exception_type(Exception),
    wait=wait_exponential(min=1, max=8),
    stop=stop_after_attempt(3),
)
def create_draft_reply(
    client: object,
    thread_id: str,
    to: str,
    subject: str,
    body: str,
) -> dict:
    """Create a draft reply in the given thread.

    CRITICAL WARNING:
    -----------------
    This function ONLY creates a draft (drafts.create). It MUST NEVER call
    drafts.send or send messages directly. The user wants to manually review
    all drafted replies inside their Gmail dashboard before sending.

    Parameters
    ----------
    client:
        Authenticated Gmail API service resource.
    thread_id:
        The thread ID of the conversation to reply to.
    to:
        The recipient email address.
    subject:
        The original subject line (will be prefixed with Re: if needed).
    body:
        The body content of the draft reply.

    Returns
    -------
    dict
        The API response dict representing the created draft.
    """
    # 1. Threading headers: Fetch the thread messages to find the parent Message-ID
    message_id_header = None
    try:
        thread = client.users().threads().get(userId="me", id=thread_id).execute()
        messages = thread.get("messages", [])
        if messages:
            # We thread under the last message in the thread
            last_msg = messages[-1]
            payload = last_msg.get("payload", {})
            for header in payload.get("headers", []):
                if header["name"].lower() == "message-id":
                    message_id_header = header["value"]
                    break
    except Exception:
        logger.warning(
            "failed_fetching_thread_message_id_for_threading",
            extra={"thread_id": thread_id},
            exc_info=True,
        )

    # 2. Build the MIME email message
    mime_msg = EmailMessage()
    mime_msg["To"] = to
    
    # Prefix subject with Re: if it doesn't already have it
    if not subject.lower().startswith("re:"):
        mime_msg["Subject"] = f"Re: {subject}"
    else:
        mime_msg["Subject"] = subject

    # Proper threading headers
    if message_id_header:
        mime_msg["In-Reply-To"] = message_id_header
        mime_msg["References"] = message_id_header

    mime_msg.set_content(body)

    # Convert to URL-safe base64 string as required by the Gmail API
    raw_bytes = mime_msg.as_bytes()
    raw = base64.urlsafe_b64encode(raw_bytes).decode("utf-8")

    draft_body = {
        "message": {
            "threadId": thread_id,
            "raw": raw,
        }
    }

    # 3. Create the draft (NEVER call send)
    logger.info(
        "creating_gmail_draft_reply",
        extra={"thread_id": thread_id, "to": to, "subject": mime_msg["Subject"]},
    )
    
    draft = client.users().drafts().create(userId="me", body=draft_body).execute()
    return draft
