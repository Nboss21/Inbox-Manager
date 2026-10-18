"""SQLite state tracking helpers."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings


def _sqlite_path(database_url: str) -> Path:
	if database_url.startswith("sqlite:///"):
		return Path(database_url.removeprefix("sqlite:///"))
	raise ValueError("DATABASE_URL must use sqlite:/// for the current scaffold")


def _ensure_parent(path: Path) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)


def get_connection() -> sqlite3.Connection:
	database_path = _sqlite_path(settings.database_url)
	_ensure_parent(database_path)
	connection = sqlite3.connect(database_path)
	connection.row_factory = sqlite3.Row
	return connection


def initialize_database() -> None:
	with get_connection() as connection:
		connection.execute(
			"""
			CREATE TABLE IF NOT EXISTS email_cursor (
				id INTEGER PRIMARY KEY CHECK (id = 1),
				cursor_value TEXT,
				cursor_type TEXT NOT NULL DEFAULT 'timestamp',
				updated_at TEXT NOT NULL
			)
			"""
		)
		connection.execute(
			"""
			CREATE TABLE IF NOT EXISTS processed_emails (
				email_id TEXT PRIMARY KEY,
				processed_at TEXT NOT NULL
			)
			"""
		)
		connection.execute(
			"""
			CREATE TABLE IF NOT EXISTS reply_processed (
				email_id TEXT PRIMARY KEY,
				processed_at TEXT NOT NULL
			)
			"""
		)
		connection.commit()


def get_email_cursor() -> str | None:
	initialize_database()
	with get_connection() as connection:
		row = connection.execute(
			"SELECT cursor_value FROM email_cursor WHERE id = 1"
		).fetchone()
		return None if row is None else row["cursor_value"]


def set_email_cursor(cursor_value: str, cursor_type: str = "timestamp") -> None:
	initialize_database()
	now = datetime.now(timezone.utc).isoformat()
	with get_connection() as connection:
		connection.execute(
			"""
			INSERT INTO email_cursor (id, cursor_value, cursor_type, updated_at)
			VALUES (1, ?, ?, ?)
			ON CONFLICT(id) DO UPDATE SET
				cursor_value = excluded.cursor_value,
				cursor_type = excluded.cursor_type,
				updated_at = excluded.updated_at
			""",
			(cursor_value, cursor_type, now),
		)
		connection.commit()


def is_email_processed(email_id: str) -> bool:
	initialize_database()
	with get_connection() as connection:
		row = connection.execute(
			"SELECT 1 FROM processed_emails WHERE email_id = ?",
			(email_id,),
		).fetchone()
		return row is not None


def mark_email_processed(email_id: str) -> None:
	initialize_database()
	now = datetime.now(timezone.utc).isoformat()
	with get_connection() as connection:
		connection.execute(
			"""
			INSERT OR IGNORE INTO processed_emails (email_id, processed_at)
			VALUES (?, ?)
			""",
			(email_id, now),
		)
		connection.commit()


def is_reply_processed(email_id: str) -> bool:
	"""Return True if this email has already been queued for a draft reply."""
	initialize_database()
	with get_connection() as connection:
		row = connection.execute(
			"SELECT 1 FROM reply_processed WHERE email_id = ?",
			(email_id,),
		).fetchone()
		return row is not None


def mark_reply_processed(email_id: str) -> None:
	"""Record that this email has been queued for a draft reply."""
	initialize_database()
	now = datetime.now(timezone.utc).isoformat()
	with get_connection() as connection:
		connection.execute(
			"""
			INSERT OR IGNORE INTO reply_processed (email_id, processed_at)
			VALUES (?, ?)
			""",
			(email_id, now),
		)
		connection.commit()
