"""Shared Site24x7 task models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Site24x7Status(StrEnum):
	UP = "UP"
	DOWN = "DOWN"
	TROUBLE = "TROUBLE"
	ERROR = "ERROR"


class Site24x7Alert(BaseModel):
	model_config = ConfigDict(extra="ignore")

	monitor_name: str = Field(default="")
	status: str = Field(default="TROUBLE")
	monitor_url: str = Field(default="")
	incident_reason: str = Field(default="")
	dashboard_link: str = Field(default="")
	timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))