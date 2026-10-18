import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings
from app.gmail.poller import run_poll_cycle
from app.tasks.webhook import router as webhook_router
from app.api.routes import router as api_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize and start the APScheduler background job
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        run_poll_cycle,
        trigger="interval",
        minutes=settings.poll_interval_minutes,
        id="gmail_poll_cycle",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc),  # Run immediately on startup
    )
    scheduler.start()
    logger.info(
        "Inbox poller scheduler started inside FastAPI lifespan; running every %s minute(s)",
        settings.poll_interval_minutes,
    )

    yield

    # Shutdown: Stop scheduler and wait for completion
    scheduler.shutdown(wait=False)
    logger.info("Inbox poller scheduler shut down gracefully")


app = FastAPI(title="Inbox Manager", lifespan=lifespan)

# CORS — allow the Next.js dev server and any configured frontend origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(webhook_router)
app.include_router(api_router)

logging.getLogger(__name__).addHandler(logging.NullHandler())


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}

