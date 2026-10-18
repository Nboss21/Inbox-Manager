"""Gemini-based classification helpers."""

from __future__ import annotations

import logging
from functools import lru_cache

from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.classification.categories import Category
from app.config import settings
from app.gmail.schemas import EmailSummary

logger = logging.getLogger(__name__)

CLASSIFICATION_MODEL = "gemini-2.5-flash"


class ClassificationResponse(BaseModel):
	category: Category = Field(description="One of PRIORITY, NEWSLETTER, MONITORING, OTHER")


@lru_cache(maxsize=1)
def _client() -> genai.Client:
	if not settings.gemini_api_key:
		raise RuntimeError("GEMINI_API_KEY is not configured")
	return genai.Client(api_key=settings.gemini_api_key)


def _build_prompt(email: EmailSummary) -> str:
	return (
		"Classify the email into exactly one category based on sender domain, subject, and snippet. "
		"Return the best match for one of these categories:\n"
		"- PRIORITY: needs the user's personal attention or action, including client emails, urgent requests, and direct questions.\n"
		"- NEWSLETTER: bulk, marketing, promotional, or subscription content.\n"
		"- MONITORING: automated alerts, system notifications, uptime incidents, and monitoring tools.\n"
		"- OTHER: anything that does not clearly fit the categories above.\n\n"
		f"Sender: {email.sender}\n"
		f"Subject: {email.subject}\n"
		f"Snippet: {email.snippet}"
	)


def _extract_category(response: ClassificationResponse | None) -> Category:
	if response is None:
		return Category.OTHER
	return response.category


@retry(
	retry=retry_if_exception_type(Exception),
	wait=wait_exponential(min=1, max=8),
	stop=stop_after_attempt(3),
)
def _classify_with_gemini(email: EmailSummary) -> ClassificationResponse:
	client = _client()
	response = client.models.generate_content(
		model=CLASSIFICATION_MODEL,
		contents=_build_prompt(email),
		config=types.GenerateContentConfig(
			system_instruction=(
				"You are a strict email triage classifier. "
				"Use the provided schema and return only valid structured output."
			),
			response_mime_type="application/json",
			response_schema=ClassificationResponse,
			temperature=0,
		),
	)

	parsed = getattr(response, "parsed", None)
	if isinstance(parsed, ClassificationResponse):
		return parsed

	text = getattr(response, "text", "")
	if text:
		return ClassificationResponse.model_validate_json(text)

	raise RuntimeError("Gemini returned an empty classification response")


def classify_email(email: EmailSummary) -> Category:
	try:
		result = _classify_with_gemini(email)
		return _extract_category(result)
	except Exception:
		logger.exception("Failed to classify email; defaulting to OTHER", extra={"email_id": email.id})
		return Category.OTHER
