"""Smoke test for the draft-reply agent.

Runs the needs_reply + draft generator against unhandled priority emails in your
real Gmail inbox, and falls back to running against 3 realistic mock emails if
no unhandled priority emails are found.

Usage (from the project root, with token.json and GEMINI_API_KEY set):

    python scripts/test_agent_replies.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.replies.agent import get_unhandled_priority_emails, process_priority_email
from app.replies.models import PriorityEmailContext


# ---------------------------------------------------------------------------
# Mock emails for fallback testing
# ---------------------------------------------------------------------------

MOCK_EMAILS = [
    PriorityEmailContext(
        thread_id="thread-mock-1",
        email_id="msg-mock-1",
        subject="Question about onboarding process",
        sender="David Miller",
        sender_email="david@clientco.com",
        snippet="Hi, we are filling out the onboarding form but we had a quick question about the data retention policy.",
        full_body="""\
Hi there,

We are currently filling out the onboarding form you sent over yesterday. We had a quick question regarding section 4 (data security): what is your standard data retention policy for database backups?

Also, could we schedule a quick call tomorrow at 2 PM EST to discuss?

Thanks,
David Miller
ClientCo Inc.
""",
        received_at=datetime.now(timezone.utc),
    ),
    PriorityEmailContext(
        thread_id="thread-mock-2",
        email_id="msg-mock-2",
        subject="FYI: Project server migration complete",
        sender="DevOps Alerts",
        sender_email="alerts@internal.net",
        snippet="The database migration to the new AWS cluster was completed successfully. All services are healthy.",
        full_body="""\
Hi,

This is an automated FYI notification that the database migration to the new AWS cluster was completed successfully at 03:00 UTC.

All services are healthy, and latencies are within normal limits. No action is required on your part.

Best,
DevOps Bot
""",
        received_at=datetime.now(timezone.utc),
    ),
    PriorityEmailContext(
        thread_id="thread-mock-3",
        email_id="msg-mock-3",
        subject="Urgent: Production API down?",
        sender="Sarah Jenkins",
        sender_email="sarah.jenkins@partnercorp.com",
        snippet="Are you seeing errors on the production endpoint? We are getting 502 Bad Gateway response codes.",
        full_body="""\
Hello,

Are you seeing errors on the production endpoint right now? We started getting 502 Bad Gateway response codes about 10 minutes ago, and our integration is currently failing.

Please let us know as soon as possible if this is a known issue on your end.

Sarah Jenkins
PartnerCorp
""",
        received_at=datetime.now(timezone.utc),
    ),
]


def _print_decision_and_draft(ctx: PriorityEmailContext, index: int, label: str) -> None:
    print(f"\n=== [{label}] Email #{index}: {ctx.subject} ===")
    print(f"From: {ctx.sender} <{ctx.sender_email}>")
    print(f"Snippet: {ctx.snippet[:80]}...")
    print("------------------------------------------------------------------------")
    print("Processing with Gemini...")

    try:
        decision = process_priority_email(ctx)
        print(f"Decision    : Needs Reply = {decision.needs_reply}")
        print(f"Reasoning   : {decision.reasoning}")
        if decision.needs_reply:
            print("Draft Reply :")
            print(decision.draft_reply)
        else:
            print("Draft Reply : (none)")
    except Exception as exc:
        print(f"Error processing email: {exc}", file=sys.stderr)
    print("========================================================================\n")


def main() -> None:
    print("\n━━━ Reply Agent Quality and Tone Triage Smoke Test ━━━")

    # Load environment variables
    # (Simple parser to avoid third-party dotenv dependency in the script)
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

    if not os.environ.get("GEMINI_API_KEY"):
        print("ERROR: GEMINI_API_KEY is not configured in .env", file=sys.stderr)
        sys.exit(1)

    # 1. Try to check real unhandled priority emails from Gmail
    real_emails = []
    print("\nChecking your Gmail account for unhandled priority emails...")
    try:
        real_emails = get_unhandled_priority_emails()
        print(f"Found {len(real_emails)} unhandled priority emails in your Gmail inbox.")
    except Exception as exc:
        print(
            f"Note: Could not fetch real Gmail messages ({exc}).\n"
            "      (Make sure token.json exists by running: python scripts/auth_gmail.py)\n"
            "      Proceeding with mock emails instead."
        )

    if real_emails:
        print("\n--- Running against your real Gmail emails ---")
        for i, ctx in enumerate(real_emails[:3], 1):
            _print_decision_and_draft(ctx, i, "REAL")
    else:
        print("\n--- Running against fallback mock emails ---")
        for i, ctx in enumerate(MOCK_EMAILS, 1):
            _print_decision_and_draft(ctx, i, "MOCK")

    print("━━━ Smoke Test Complete ━━━\n")


if __name__ == "__main__":
    main()
