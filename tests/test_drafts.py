"""Unit tests for Gmail draft reply creation."""

from __future__ import annotations

import base64
from email import policy
from email.parser import BytesParser
from unittest.mock import MagicMock, patch

import pytest

from app.gmail.drafts import create_draft_reply


class TestCreateDraftReply:

    @patch("app.gmail.drafts.logging")
    def test_create_draft_reply_success(self, mock_logging: MagicMock) -> None:
        # Mock Gmail client
        mock_client = MagicMock()
        
        # Mock thread retrieval
        mock_thread = {
            "messages": [
                {
                    "payload": {
                        "headers": [
                            {"name": "Message-ID", "value": "<parent-msg-123@mail.gmail.com>"}
                        ]
                    }
                }
            ]
        }
        mock_client.users().threads().get().execute.return_value = mock_thread
        
        # Mock draft creation response
        mock_draft_response = {"id": "draft-abc-123", "message": {"threadId": "thread-123"}}
        mock_client.users().drafts().create().execute.return_value = mock_draft_response

        # Execute
        result = create_draft_reply(
            client=mock_client,
            thread_id="thread-123",
            to="receiver@example.com",
            subject="Question about pricing",
            body="Here is the body text.",
        )

        assert result == mock_draft_response
        
        # Assert database query structure and correct parameters
        mock_client.users().threads().get.assert_any_call(userId="me", id="thread-123")
        
        # Verify the create request payload
        create_call = mock_client.users().drafts().create.call_args
        assert create_call is not None
        body_arg = create_call.kwargs["body"]
        assert body_arg["message"]["threadId"] == "thread-123"
        
        # Decode the raw email to verify headers and content
        raw_email_b64 = body_arg["message"]["raw"]
        raw_email_bytes = base64.urlsafe_b64decode(raw_email_b64)
        parsed_email = BytesParser(policy=policy.default).parsebytes(raw_email_bytes)
        
        assert parsed_email["To"] == "receiver@example.com"
        assert parsed_email["Subject"] == "Re: Question about pricing"
        assert parsed_email["In-Reply-To"] == "<parent-msg-123@mail.gmail.com>"
        assert parsed_email["References"] == "<parent-msg-123@mail.gmail.com>"
        assert parsed_email.get_content().strip() == "Here is the body text."

    @patch("app.gmail.drafts.logging")
    def test_create_draft_reply_no_subject_prefix_duplication(self, mock_logging: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client.users().threads().get().execute.side_effect = Exception("Thread not found")
        mock_client.users().drafts().create().execute.return_value = {"id": "draft-abc-123"}

        create_draft_reply(
            client=mock_client,
            thread_id="thread-123",
            to="receiver@example.com",
            subject="Re: Already has prefix",
            body="Replying again.",
        )

        create_call = mock_client.users().drafts().create.call_args
        raw_email_b64 = create_call.kwargs["body"]["message"]["raw"]
        raw_email_bytes = base64.urlsafe_b64decode(raw_email_b64)
        parsed_email = BytesParser(policy=policy.default).parsebytes(raw_email_bytes)

        assert parsed_email["Subject"] == "Re: Already has prefix"
