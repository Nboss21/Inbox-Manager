"""Tests for app/tasks/notion_client.py and webhook → Notion wiring."""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

import pytest
from fastapi.testclient import TestClient
from notion_client.errors import APIResponseError

from app.main import app
from app.tasks.models import Site24x7Alert
from app.tasks import notion_client as nc

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIXTURE = Path(__file__).parent / "fixtures" / "site24x7_down.json"

_DOWN_ALERT = Site24x7Alert(
    monitor_name="API Health Check",
    status="DOWN",
    monitor_url="https://api.example.com/health",
    incident_reason="HTTP 500 returned by the target endpoint",
    dashboard_link="https://www.site24x7.com/app/monitor-details/123456",
    timestamp=datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc),
)

_FAKE_PAGE_RESPONSE = {
    "id": "abc-123",
    "url": "https://www.notion.so/abc-123",
}


def _make_api_error() -> APIResponseError:
    """Construct a minimal APIResponseError for testing retry logic."""
    response = MagicMock()
    response.status_code = 503
    response.text = "Service Unavailable"
    return APIResponseError(response=response, message="Service Unavailable", code="service_unavailable")


# ---------------------------------------------------------------------------
# 1. Payload shape
# ---------------------------------------------------------------------------

class TestBuildProperties:
    """Unit-test the property dict that gets sent to Notion."""

    def test_title_contains_monitor_name_and_status(self) -> None:
        props = nc._build_properties(_DOWN_ALERT)
        title_text = props["Name"]["title"][0]["text"]["content"]
        assert "[INCIDENT]" in title_text
        assert "API Health Check" in title_text
        assert "DOWN" in title_text

    def test_priority_is_high(self) -> None:
        props = nc._build_properties(_DOWN_ALERT)
        assert props["Priority"]["select"]["name"] == "High"

    def test_status_is_to_do(self) -> None:
        props = nc._build_properties(_DOWN_ALERT)
        assert props["Status"]["status"]["name"] == "To Do"

    def test_assignee_omitted_when_env_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.tasks.notion_client.settings.default_assignee_notion_id", "")
        props = nc._build_properties(_DOWN_ALERT)
        assert "Assignee" not in props

    def test_assignee_included_when_env_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.tasks.notion_client.settings.default_assignee_notion_id",
            "user-uuid-1234",
        )
        props = nc._build_properties(_DOWN_ALERT)
        assert props["Assignee"]["people"][0]["id"] == "user-uuid-1234"


class TestBuildBodyBlocks:
    """Unit-test the Notion block list that forms the page body."""

    def test_blocks_contain_incident_reason(self) -> None:
        blocks = nc._build_body_blocks(_DOWN_ALERT)
        all_text = _flatten_block_text(blocks)
        assert "HTTP 500" in all_text

    def test_blocks_contain_monitor_url(self) -> None:
        blocks = nc._build_body_blocks(_DOWN_ALERT)
        all_text = _flatten_block_text(blocks)
        assert "api.example.com" in all_text

    def test_blocks_contain_dashboard_link(self) -> None:
        blocks = nc._build_body_blocks(_DOWN_ALERT)
        all_text = _flatten_block_text(blocks)
        assert "site24x7.com" in all_text

    def test_blocks_contain_timestamp(self) -> None:
        blocks = nc._build_body_blocks(_DOWN_ALERT)
        all_text = _flatten_block_text(blocks)
        assert "2026-07-10" in all_text

    def test_blocks_include_heading(self) -> None:
        blocks = nc._build_body_blocks(_DOWN_ALERT)
        types = [b["type"] for b in blocks]
        assert "heading_2" in types

    def test_blocks_include_callout(self) -> None:
        blocks = nc._build_body_blocks(_DOWN_ALERT)
        types = [b["type"] for b in blocks]
        assert "callout" in types


def _flatten_block_text(blocks: list[dict]) -> str:
    """Extract all text content from a list of Notion block dicts."""
    parts: list[str] = []
    for block in blocks:
        block_type = block.get("type", "")
        inner = block.get(block_type, {})
        for rt in inner.get("rich_text", []):
            parts.append(rt.get("text", {}).get("content", ""))
    return " ".join(parts)


# ---------------------------------------------------------------------------
# 2. Happy-path SDK call
# ---------------------------------------------------------------------------

class TestCreateIncidentTaskSuccess:

    @patch("app.tasks.notion_client.Client")
    def test_calls_pages_create_with_correct_args(
        self, MockClient: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.tasks.notion_client.settings.notion_api_key", "test-key")
        monkeypatch.setattr("app.tasks.notion_client.settings.notion_database_id", "db-id-999")
        monkeypatch.setattr("app.tasks.notion_client.settings.default_assignee_notion_id", "")

        mock_instance = MockClient.return_value
        mock_instance.pages.create.return_value = _FAKE_PAGE_RESPONSE

        result = nc.create_incident_task(_DOWN_ALERT)

        assert result == "https://www.notion.so/abc-123"
        create_call = mock_instance.pages.create.call_args
        assert create_call.kwargs["parent"] == {"database_id": "db-id-999"}
        # Title check
        title_text = create_call.kwargs["properties"]["Name"]["title"][0]["text"]["content"]
        assert "[INCIDENT] API Health Check is DOWN" == title_text
        # Body blocks present
        assert len(create_call.kwargs["children"]) >= 3


# ---------------------------------------------------------------------------
# 3. Retry logic
# ---------------------------------------------------------------------------

class TestRetryBehaviour:

    @patch("app.tasks.notion_client.Client")
    def test_retries_on_api_error_then_succeeds(
        self, MockClient: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SDK raises APIResponseError twice, succeeds on third attempt."""
        monkeypatch.setattr("app.tasks.notion_client.settings.notion_api_key", "test-key")
        monkeypatch.setattr("app.tasks.notion_client.settings.notion_database_id", "db-id")
        monkeypatch.setattr("app.tasks.notion_client.settings.default_assignee_notion_id", "")

        err = _make_api_error()
        mock_instance = MockClient.return_value
        mock_instance.pages.create.side_effect = [err, err, _FAKE_PAGE_RESPONSE]

        result = nc.create_incident_task(_DOWN_ALERT)

        assert result == "https://www.notion.so/abc-123"
        assert mock_instance.pages.create.call_count == 3

    @patch("app.tasks.notion_client.Client")
    def test_raises_after_max_retries_exhausted(
        self, MockClient: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SDK always fails → exception propagates after 3 attempts."""
        monkeypatch.setattr("app.tasks.notion_client.settings.notion_api_key", "test-key")
        monkeypatch.setattr("app.tasks.notion_client.settings.notion_database_id", "db-id")
        monkeypatch.setattr("app.tasks.notion_client.settings.default_assignee_notion_id", "")

        err = _make_api_error()
        mock_instance = MockClient.return_value
        mock_instance.pages.create.side_effect = err

        with pytest.raises(APIResponseError):
            nc.create_incident_task(_DOWN_ALERT)

        assert mock_instance.pages.create.call_count == 3

    @patch("app.tasks.notion_client.Client")
    def test_error_is_logged_on_final_failure(
        self, MockClient: MagicMock, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr("app.tasks.notion_client.settings.notion_api_key", "test-key")
        monkeypatch.setattr("app.tasks.notion_client.settings.notion_database_id", "db-id")
        monkeypatch.setattr("app.tasks.notion_client.settings.default_assignee_notion_id", "")

        mock_instance = MockClient.return_value
        mock_instance.pages.create.side_effect = _make_api_error()

        import logging
        with caplog.at_level(logging.ERROR, logger="app.tasks.notion_client"):
            with pytest.raises(APIResponseError):
                nc.create_incident_task(_DOWN_ALERT)

        assert any("notion_task_creation_failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 4. Webhook integration: DOWN fixture → create_incident_task called
# ---------------------------------------------------------------------------

class TestWebhookNotionWiring:

    @patch("app.tasks.webhook.create_incident_task")
    def test_down_alert_triggers_notion_task(
        self, mock_create: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """POSTing the DOWN fixture with a valid secret → create_incident_task called once."""
        monkeypatch.setattr(
            "app.tasks.webhook.settings.site24x7_webhook_secret", "test-secret"
        )
        mock_create.return_value = "https://www.notion.so/fake-page"

        client = TestClient(app)
        payload = json.loads(_FIXTURE.read_text())

        response = client.post(
            "/webhooks/site24x7",
            json=payload,
            headers={"X-Site24x7-Secret": "test-secret"},
        )

        assert response.status_code == 200
        assert response.json() == {"status": "accepted"}
        # TestClient runs background tasks synchronously
        mock_create.assert_called_once()
        called_alert: Site24x7Alert = mock_create.call_args.args[0]
        assert called_alert.status == "DOWN"
        assert called_alert.monitor_name == "API Health Check"

    @patch("app.tasks.webhook.create_incident_task")
    def test_up_alert_does_not_trigger_notion_task(
        self, mock_create: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.tasks.webhook.settings.site24x7_webhook_secret", "test-secret"
        )
        client = TestClient(app)

        response = client.post(
            "/webhooks/site24x7",
            json={"monitor_name": "API Health Check", "status": "UP"},
            headers={"X-Site24x7-Secret": "test-secret"},
        )

        assert response.status_code == 200
        assert response.json() == {"status": "ignored"}
        mock_create.assert_not_called()
