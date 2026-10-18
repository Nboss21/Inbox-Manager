"""Gmail API client helpers."""

from __future__ import annotations

import base64
import html.parser
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings

logger = logging.getLogger(__name__)

SCOPES = [
	"https://www.googleapis.com/auth/gmail.readonly",
	"https://www.googleapis.com/auth/gmail.modify",
]


@dataclass(slots=True)
class GmailClientBundle:
	credentials: Credentials
	service: object


def _credentials_path() -> Path:
	return Path(settings.gmail_credentials_file)


def _token_path() -> Path:
	return Path(settings.gmail_token_file)


def _load_credentials() -> Credentials | None:
	token_path = _token_path()
	if token_path.exists():
		return Credentials.from_authorized_user_file(str(token_path), SCOPES)
	return None


def _save_credentials(credentials: Credentials) -> None:
	token_path = _token_path()
	token_path.parent.mkdir(parents=True, exist_ok=True)
	token_path.write_text(credentials.to_json(), encoding="utf-8")


def authenticate(interactive: bool = True) -> Credentials:
	credentials = _load_credentials()
	if credentials and credentials.expired and credentials.refresh_token:
		logger.info("Refreshing Gmail credentials")
		credentials.refresh(Request())
		_save_credentials(credentials)
		return credentials

	if credentials and credentials.valid:
		return credentials

	credentials_path = _credentials_path()
	if not credentials_path.exists():
		raise FileNotFoundError(
			f"Gmail credentials file not found: {credentials_path.resolve()}"
		)

	if not interactive:
		raise RuntimeError("No valid Gmail token available and interactive auth disabled")

	logger.info("Starting first-time Gmail OAuth flow")
	flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
	credentials = flow.run_local_server(port=0)
	_save_credentials(credentials)
	return credentials


@retry(
	retry=retry_if_exception_type(Exception),
	wait=wait_exponential(min=1, max=8),
	stop=stop_after_attempt(3),
)
def build_gmail_service(interactive: bool = True) -> GmailClientBundle:
	credentials = authenticate(interactive=interactive)
	service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
	return GmailClientBundle(credentials=credentials, service=service)


# ---------------------------------------------------------------------------
# Full message body helpers
# ---------------------------------------------------------------------------


class _HTMLStripper(html.parser.HTMLParser):
	"""Minimal HTML → plain-text converter (no external deps)."""

	def __init__(self) -> None:
		super().__init__()
		self._parts: list[str] = []

	def handle_data(self, data: str) -> None:
		stripped = data.strip()
		if stripped:
			self._parts.append(stripped)

	def handle_starttag(self, tag: str, attrs: object) -> None:
		# Insert a newline before block-level tags so paragraphs aren't joined.
		if tag in {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
			self._parts.append("\n")

	def get_text(self) -> str:
		text = " ".join(self._parts)
		# Collapse runs of whitespace / blank lines
		text = re.sub(r" {2,}", " ", text)
		text = re.sub(r"\n{3,}", "\n\n", text)
		return text.strip()


def _strip_html(html_text: str) -> str:
	stripper = _HTMLStripper()
	stripper.feed(html_text)
	return stripper.get_text()


def _decode_part_data(data: str) -> str:
	"""Decode a Gmail message part's base64url-encoded body data."""
	# Gmail uses URL-safe base64; add padding if needed.
	padded = data.replace("-", "+").replace("_", "/")
	padded += "=" * (-len(padded) % 4)
	return base64.b64decode(padded).decode("utf-8", errors="replace")


def _extract_text_from_parts(parts: list[dict]) -> tuple[str, str]:
	"""Recursively walk MIME parts; return (plain_text, html_text)."""
	plain: list[str] = []
	html_parts: list[str] = []

	for part in parts:
		mime_type = part.get("mimeType", "")
		sub_parts = part.get("parts", [])
		body_data = part.get("body", {}).get("data", "")

		if sub_parts:
			sub_plain, sub_html = _extract_text_from_parts(sub_parts)
			plain.append(sub_plain)
			html_parts.append(sub_html)
		elif mime_type == "text/plain" and body_data:
			plain.append(_decode_part_data(body_data))
		elif mime_type == "text/html" and body_data:
			html_parts.append(_decode_part_data(body_data))

	return "\n".join(plain).strip(), "\n".join(html_parts).strip()


def _body_from_payload(payload: dict) -> str:
	"""Extract the best available body text from a Gmail message payload."""
	# Single-part message
	body_data = payload.get("body", {}).get("data", "")
	mime_type = payload.get("mimeType", "")

	if body_data:
		raw = _decode_part_data(body_data)
		return _strip_html(raw) if "html" in mime_type else raw

	# Multi-part message
	parts = payload.get("parts", [])
	if parts:
		plain, html_text = _extract_text_from_parts(parts)
		if plain:
			return plain
		if html_text:
			return _strip_html(html_text)

	return ""


@retry(
	retry=retry_if_exception_type(Exception),
	wait=wait_exponential(min=1, max=8),
	stop=stop_after_attempt(3),
)
def get_full_message(email_id: str, service: object | None = None) -> str:
	"""Fetch and decode the full body text of a Gmail message.

	Parameters
	----------
	email_id:
	    The Gmail message ID to fetch.
	service:
	    An already-authenticated Gmail service object.  If omitted, a new
	    authenticated service is built (requires a valid token.json).

	Returns
	-------
	str
	    Decoded plain-text body.  HTML is stripped to readable text.
	    Returns an empty string if the body is absent or cannot be decoded.
	"""
	if service is None:
		bundle = build_gmail_service(interactive=False)
		service = bundle.service

	message = (
		service.users()
		.messages()
		.get(userId="me", id=email_id, format="full")
		.execute()
	)
	payload = message.get("payload", {})
	return _body_from_payload(payload)
