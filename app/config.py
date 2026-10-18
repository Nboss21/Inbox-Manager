"""Application settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Inbox Manager"
    app_env: str = "development"
    log_level: str = "INFO"
    gmail_credentials_file: str = "credentials.json"
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_refresh_token: str = ""
    gmail_token_file: str = "token.json"
    gemini_api_key: str = ""
    notion_api_key: str = ""
    notion_database_id: str = ""
    default_assignee_notion_id: str = ""
    site24x7_webhook_secret: str = ""
    database_url: str = "sqlite:///./data/inbox_manager.db"
    poll_interval_minutes: int = 5
    gmail_user_id: str = "me"


settings = Settings()
