"""Run the first-time Gmail OAuth flow and cache token.json locally."""

from app.gmail.client import authenticate


if __name__ == "__main__":
    authenticate(interactive=True)
    print("Gmail authentication completed.")