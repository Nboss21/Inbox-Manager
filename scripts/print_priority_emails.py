"""Quick smoke test: print PriorityEmailContext for unhandled priority emails.

Usage (from the project root, with a valid token.json present):

    python scripts/print_priority_emails.py

    # Show at most N emails (default: all)
    python scripts/print_priority_emails.py --limit 1

    # Show the full body text (can be long)
    python scripts/print_priority_emails.py --full-body
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

# Make sure the project root is on sys.path when run directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.replies.agent import get_unhandled_priority_emails


_DIVIDER = "─" * 72


def _print_context(ctx, *, show_full_body: bool) -> None:
    print(_DIVIDER)
    print(f"  email_id  : {ctx.email_id}")
    print(f"  thread_id : {ctx.thread_id}")
    print(f"  subject   : {ctx.subject}")
    print(f"  sender    : {ctx.sender} <{ctx.sender_email}>")
    print(f"  received  : {ctx.received_at.isoformat()}")
    print(f"  snippet   : {ctx.snippet[:120]}")

    if show_full_body:
        print()
        print("  ── full_body ──")
        body_preview = ctx.full_body or "(empty)"
        # Indent and wrap to 80 chars for readability
        wrapped = textwrap.fill(body_preview, width=76, initial_indent="  ", subsequent_indent="  ")
        print(wrapped[:3000])  # cap at 3000 chars to avoid flooding the terminal
        if len(ctx.full_body) > 3000:
            print("  … (truncated)")
    else:
        body_len = len(ctx.full_body)
        print(f"  full_body : {body_len} chars (use --full-body to display)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print PriorityEmailContext for unhandled priority emails"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Stop after N emails (default: all)",
    )
    parser.add_argument(
        "--full-body",
        action="store_true",
        help="Print the full decoded body text",
    )
    args = parser.parse_args()

    print("\nFetching unhandled priority emails …\n")
    try:
        contexts = get_unhandled_priority_emails()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(
            "\nMake sure token.json exists. Run:  python scripts/auth_gmail.py",
            file=sys.stderr,
        )
        sys.exit(1)

    if not contexts:
        print("No unhandled priority emails found.")
        print(
            "(Either the inbox has none, or they've all been mark_reply_processed'd.)"
        )
        return

    limit = args.limit or len(contexts)
    for ctx in contexts[:limit]:
        _print_context(ctx, show_full_body=args.full_body)

    print(_DIVIDER)
    print(f"\nTotal shown: {min(limit, len(contexts))} / {len(contexts)} unhandled\n")


if __name__ == "__main__":
    main()
