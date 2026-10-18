"""Smoke test for the Gemini email classifier."""

from app.classification.classifier import classify_email
from app.gmail.schemas import EmailSummary


EXAMPLES = [
    EmailSummary(
        id="ex-1",
        thread_id="t-1",
        sender="alex@clientco.com",
        subject="Need your approval on the revised proposal",
        snippet="Can you review the attached proposal and confirm by end of day?",
        received_at="2026-07-10T09:00:00+00:00",
        label_ids=[],
    ),
    EmailSummary(
        id="ex-2",
        thread_id="t-2",
        sender="newsletter@productupdates.com",
        subject="This week in product updates",
        snippet="Here are the latest announcements, feature releases, and offers.",
        received_at="2026-07-10T09:05:00+00:00",
        label_ids=[],
    ),
    EmailSummary(
        id="ex-3",
        thread_id="t-3",
        sender="alerts@site24x7.com",
        subject="Monitor API latency is DOWN",
        snippet="The endpoint has exceeded the threshold and is currently unavailable.",
        received_at="2026-07-10T09:10:00+00:00",
        label_ids=[],
    ),
    EmailSummary(
        id="ex-4",
        thread_id="t-4",
        sender="billing@stripe.com",
        subject="Your receipt for June",
        snippet="A receipt for your recent payment is attached.",
        received_at="2026-07-10T09:15:00+00:00",
        label_ids=[],
    ),
    EmailSummary(
        id="ex-5",
        thread_id="t-5",
        sender="support@vendor.io",
        subject="Quick question about your onboarding setup",
        snippet="We need a small clarification to complete setup.",
        received_at="2026-07-10T09:20:00+00:00",
        label_ids=[],
    ),
    EmailSummary(
        id="ex-6",
        thread_id="t-6",
        sender="noreply@social.example",
        subject="Someone mentioned you in a post",
        snippet="You have a new notification from your social network.",
        received_at="2026-07-10T09:25:00+00:00",
        label_ids=[],
    ),
]


def main() -> None:
    for email in EXAMPLES:
        category = classify_email(email)
        print(f"{email.id}: {category.value} | {email.sender} | {email.subject}")


if __name__ == "__main__":
    main()