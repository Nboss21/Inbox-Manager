"""
Integration test: webhook → Notion pipeline.

Posts a fake DOWN alert to a running local server, then queries Notion back to
confirm that a page was created with the right properties.

Usage (from the project root, with the server running in another terminal):

    python scripts/test_incident_flow.py

Environment variables required (in .env or shell):
    NOTION_API_KEY            — Notion internal integration secret
    NOTION_DATABASE_ID        — ID of the target database
    SITE24X7_WEBHOOK_SECRET   — shared secret configured on the webhook endpoint

Optional:
    WEBHOOK_BASE_URL          — defaults to http://localhost:8000
    TEST_MONITOR_NAME         — defaults to "Integration Test Monitor"
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Load .env if present (no external dependency — plain file parsing)
# ---------------------------------------------------------------------------

def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

WEBHOOK_BASE_URL  = os.environ.get("WEBHOOK_BASE_URL", "http://localhost:8000").rstrip("/")
NOTION_API_KEY    = os.environ.get("NOTION_API_KEY", "")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "")
WEBHOOK_SECRET    = os.environ.get("SITE24X7_WEBHOOK_SECRET", "")
MONITOR_NAME      = os.environ.get("TEST_MONITOR_NAME", "Integration Test Monitor")

NOTION_VERSION    = "2022-06-28"
SEARCH_WAIT_S     = 5   # seconds to wait before querying Notion (background task lag)
SEARCH_RETRIES    = 3   # how many times to retry the Notion search
SEARCH_INTERVAL_S = 3   # seconds between retries

EXPECTED_TITLE    = f"[INCIDENT] {MONITOR_NAME} is DOWN"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _assert_config() -> None:
    missing = [k for k, v in {
        "NOTION_API_KEY": NOTION_API_KEY,
        "NOTION_DATABASE_ID": NOTION_DATABASE_ID,
        "SITE24X7_WEBHOOK_SECRET": WEBHOOK_SECRET,
    }.items() if not v]
    if missing:
        _fail(f"Missing required environment variables: {', '.join(missing)}")


def _ok(msg: str) -> None:
    print(f"  ✓  {msg}")


def _info(msg: str) -> None:
    print(f"  …  {msg}")


def _fail(msg: str) -> None:
    print(f"\n  ✗  FAIL: {msg}\n", file=sys.stderr)
    sys.exit(1)


def _http_post(url: str, payload: dict, headers: dict) -> dict:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        _fail(f"POST {url} → HTTP {exc.code}: {exc.read().decode()[:300]}")
    except urllib.error.URLError as exc:
        _fail(
            f"Cannot reach {url}: {exc.reason}\n"
            "     Make sure the server is running:  uvicorn app.main:app --reload"
        )


def _notion_get(path: str) -> dict:
    url = f"https://api.notion.com/v1{path}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {NOTION_API_KEY}",
            "Notion-Version": NOTION_VERSION,
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _notion_post(path: str, payload: dict) -> dict:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"https://api.notion.com/v1{path}",
        data=body,
        headers={
            "Authorization": f"Bearer {NOTION_API_KEY}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def step_check_server() -> None:
    _info("Checking local server is alive …")
    try:
        req = urllib.request.Request(f"{WEBHOOK_BASE_URL}/health", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            assert data.get("status") == "ok", f"Unexpected health response: {data}"
    except urllib.error.URLError as exc:
        _fail(
            f"Server not reachable at {WEBHOOK_BASE_URL}: {exc.reason}\n"
            "     Start it with:  uvicorn app.main:app --reload"
        )
    _ok("Server is up")


def step_post_webhook() -> str:
    """POST the fake DOWN alert. Returns the ISO timestamp used in the payload."""
    _info("Posting fake DOWN alert to webhook …")

    timestamp = datetime.now(timezone.utc).isoformat()
    payload = {
        "monitor_name": MONITOR_NAME,
        "status": "DOWN",
        "monitor_url": "https://api.example.com/health",
        "incident_reason": "Integration test — HTTP 500 from target endpoint",
        "dashboard_link": "https://www.site24x7.com/app/monitor-details/test",
        "timestamp": timestamp,
    }
    response = _http_post(
        f"{WEBHOOK_BASE_URL}/webhooks/site24x7",
        payload,
        headers={
            "Content-Type": "application/json",
            "X-Site24x7-Secret": WEBHOOK_SECRET,
        },
    )
    if response.get("status") != "accepted":
        _fail(f"Webhook did not accept the alert: {response}")
    _ok(f"Webhook accepted  (timestamp={timestamp})")
    return timestamp


def step_find_notion_page() -> dict:
    """
    Query Notion for a page with the expected title, retrying a few times
    to allow the background task + Notion API to settle.
    """
    _info(f"Waiting {SEARCH_WAIT_S}s for background task to hit Notion …")
    time.sleep(SEARCH_WAIT_S)

    for attempt in range(1, SEARCH_RETRIES + 1):
        _info(f"Querying Notion database (attempt {attempt}/{SEARCH_RETRIES}) …")
        result = _notion_post(
            f"/databases/{NOTION_DATABASE_ID}/query",
            {
                "filter": {
                    "property": "Name",
                    "title": {"equals": EXPECTED_TITLE},
                },
                "page_size": 5,
                "sorts": [{"timestamp": "created_time", "direction": "descending"}],
            },
        )
        pages = result.get("results", [])
        if pages:
            _ok(f"Found {len(pages)} matching page(s) in Notion")
            return pages[0]  # most-recent match
        if attempt < SEARCH_RETRIES:
            _info(f"Not found yet — waiting {SEARCH_INTERVAL_S}s before retry …")
            time.sleep(SEARCH_INTERVAL_S)

    _fail(
        f"No Notion page with title '{EXPECTED_TITLE}' found after "
        f"{SEARCH_RETRIES} attempts.\n"
        "     Check server logs for 'notion_task_creation_failed'."
    )


def step_assert_properties(page: dict) -> None:
    _info("Asserting page properties …")
    props = page.get("properties", {})

    # --- Title ---
    title_parts = props.get("Name", {}).get("title", [])
    title_text = "".join(t.get("plain_text", "") for t in title_parts)
    if title_text != EXPECTED_TITLE:
        _fail(f"Title mismatch.\n     Expected: {EXPECTED_TITLE!r}\n     Got:      {title_text!r}")
    _ok(f"Title  = {title_text!r}")

    # --- Priority ---
    priority = props.get("Priority", {}).get("select", {}).get("name")
    if priority != "High":
        _fail(f"Priority expected 'High', got {priority!r}")
    _ok(f"Priority = {priority!r}")

    # --- Status ---
    # Handles both native Status type and a plain Select fallback
    status_obj = props.get("Status", {})
    status_name = (
        status_obj.get("status", {}).get("name")
        or status_obj.get("select", {}).get("name")
    )
    if status_name != "To Do":
        _fail(f"Status expected 'To Do', got {status_name!r}")
    _ok(f"Status   = {status_name!r}")


def step_assert_body(page: dict) -> None:
    _info("Fetching page blocks to verify body …")
    page_id = page["id"]
    result = _notion_get(f"/blocks/{page_id}/children")
    blocks = result.get("results", [])

    all_text = " ".join(
        part.get("plain_text", "")
        for block in blocks
        for rich_text_list in [
            block.get(block.get("type", ""), {}).get("rich_text", [])
        ]
        for part in rich_text_list
    )

    checks = {
        "incident reason": "Integration test",
        "monitor URL": "api.example.com",
        "dashboard link": "site24x7.com",
        "timestamp callout": "Detected at",
    }
    for label, fragment in checks.items():
        if fragment not in all_text:
            _fail(f"Body missing {label} (expected fragment {fragment!r}).\nFull text: {all_text[:400]}")
        _ok(f"Body contains {label}")


def step_print_page_url(page: dict) -> None:
    url = page.get("url", "(no url)")
    print(f"\n  🔗  Notion page URL: {url}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("\n━━━ Incident Flow Integration Test ━━━\n")
    _assert_config()

    step_check_server()
    step_post_webhook()
    page = step_find_notion_page()
    step_assert_properties(page)
    step_assert_body(page)
    step_print_page_url(page)

    print("━━━ All assertions passed ✓ ━━━\n")


if __name__ == "__main__":
    main()
