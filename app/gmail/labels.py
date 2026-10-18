"""Gmail label helpers."""

from __future__ import annotations

import logging

from googleapiclient.errors import HttpError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.classification.categories import CATEGORY_LABELS, Category

logger = logging.getLogger(__name__)


def _list_labels(client: object) -> list[dict]:
	response = client.users().labels().list(userId="me").execute()
	return response.get("labels", [])


def _find_label_id(labels: list[dict], label_name: str) -> str | None:
	for label in labels:
		if label.get("name") == label_name:
			return label.get("id")
	return None


@retry(
	retry=retry_if_exception_type(Exception),
	wait=wait_exponential(min=1, max=8),
	stop=stop_after_attempt(3),
)
def ensure_labels_exist(client: object) -> dict[Category, str]:
	"""Ensure the category labels exist in Gmail and return their ids."""

	existing_labels = _list_labels(client)
	label_ids: dict[Category, str] = {}

	for category, label_name in CATEGORY_LABELS.items():
		label_id = _find_label_id(existing_labels, label_name)
		if label_id is None:
			try:
				created = (
					client.users()
					.labels()
					.create(
						userId="me",
						body={
							"name": label_name,
							"labelListVisibility": "labelShow",
							"messageListVisibility": "show",
						},
					)
					.execute()
				)
			except HttpError:
				logger.exception("Failed to create Gmail label", extra={"label_name": label_name})
				raise
			label_id = created["id"]
			existing_labels.append(created)

		label_ids[category] = label_id

	return label_ids


@retry(
	retry=retry_if_exception_type(Exception),
	wait=wait_exponential(min=1, max=8),
	stop=stop_after_attempt(3),
)
def apply_label(client: object, email_id: str, category: Category) -> str:
	"""Apply the Gmail label that corresponds to the category."""

	label_name = CATEGORY_LABELS[category]
	labels = _list_labels(client)
	label_id = _find_label_id(labels, label_name)
	if label_id is None:
		raise RuntimeError(f"Gmail label does not exist: {label_name}")

	client.users().messages().modify(
		userId="me",
		id=email_id,
		body={"addLabelIds": [label_id]},
	).execute()
	return label_name
