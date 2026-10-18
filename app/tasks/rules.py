"""Task decision rules for Site24x7 alerts."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from app.tasks.models import Site24x7Alert


def should_create_task(alert: "Site24x7Alert") -> bool:
	"""Return True only when the alert indicates a new incident."""

	status = alert.status.strip().upper()
	return status in {"DOWN", "TROUBLE", "ERROR"}