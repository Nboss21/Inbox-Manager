"""Run a single Gmail poll and print new messages."""

import logging
import sys

from pythonjsonlogger import jsonlogger

from app.gmail.poller import main


def _configure_logging() -> None:
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s %(email_id)s %(sender)s %(subject)s %(category)s %(label_applied)s")
    )
    root_logger.handlers.clear()
    root_logger.addHandler(handler)


if __name__ == "__main__":
    _configure_logging()
    main()