"""Scheduled Gmail polling service."""

from __future__ import annotations

import logging
import signal
import time
from threading import Event

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings
from app.gmail.poller import run_poll_cycle

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def run_service() -> None:
    _configure_logging()
    stop_event = Event()

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        run_poll_cycle,
        trigger="interval",
        minutes=settings.poll_interval_minutes,
        id="gmail_poll_cycle",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.start()

    logger.info(
        "Inbox poller service started; running every %s minute(s)",
        settings.poll_interval_minutes,
    )

    def _shutdown_handler(*_: object) -> None:
        logger.info("Inbox poller service shutting down")
        stop_event.set()
        scheduler.shutdown(wait=False)

    signal.signal(signal.SIGINT, _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)

    try:
        while not stop_event.is_set():
            time.sleep(1)
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)


if __name__ == "__main__":
    run_service()