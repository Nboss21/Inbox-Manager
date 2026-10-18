"""Site24x7 webhook routes."""

from __future__ import annotations

import logging
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status

from app.config import settings
from app.tasks.models import Site24x7Alert
from app.tasks.notion_client import create_incident_task
from app.tasks.rules import should_create_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _verify_secret(shared_secret: str | None) -> None:
	if not settings.site24x7_webhook_secret:
		raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Webhook secret is not configured")

	if shared_secret != settings.site24x7_webhook_secret:
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized webhook call")


def _process_alert(alert: Site24x7Alert) -> None:
	logger.info(
		"site24x7_alert_received",
		extra={
			"monitor_name": alert.monitor_name,
			"status": alert.status,
			"monitor_url": alert.monitor_url,
			"incident_reason": alert.incident_reason,
			"dashboard_link": alert.dashboard_link,
			"timestamp": alert.timestamp.isoformat(),
		},
	)

	try:
		page_url = create_incident_task(alert)
		logger.info(
			"site24x7_notion_task_queued",
			extra={"page_url": page_url, "monitor_name": alert.monitor_name},
		)
	except Exception:
		# create_incident_task already logs at ERROR with full context + exc_info.
		# We catch here so the background task doesn't die silently without a trace.
		logger.error(
			"site24x7_notion_task_failed_unrecoverable",
			extra={"monitor_name": alert.monitor_name, "status": alert.status},
			exc_info=True,
		)


@router.post("/site24x7")
async def site24x7_webhook(
	request: Request,
	background_tasks: BackgroundTasks,
	x_site24x7_secret: str | None = Header(default=None, alias="X-Site24x7-Secret"),
) -> dict[str, str]:
	_verify_secret(x_site24x7_secret)

	payload = await request.json()
	alert = Site24x7Alert.model_validate(payload)
	if not should_create_task(alert):
		logger.info(
			"site24x7_alert_ignored",
			extra={
				"monitor_name": alert.monitor_name,
				"status": alert.status,
				"monitor_url": alert.monitor_url,
				"timestamp": alert.timestamp.isoformat(),
			},
		)
		return {"status": "ignored"}

	background_tasks.add_task(_process_alert, alert)
	return {"status": "accepted"}
