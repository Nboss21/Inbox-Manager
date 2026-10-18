"""Gmail polling schemas."""

from pydantic import BaseModel


class EmailSummary(BaseModel):
    id: str
    thread_id: str
    sender: str
    subject: str
    snippet: str
    received_at: str
    label_ids: list[str]