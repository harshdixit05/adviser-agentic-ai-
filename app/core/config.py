from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env.local", extra="ignore"
    )

    database_url: str = "postgresql+psycopg2://postgres@127.0.0.1:55432/advisor"

    # Never committed; read from .env.local or the environment.
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.0-flash"
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "intellimindz_content"

    # Shared secret between the website's server and this service. When set,
    # /api/chat requires it — and only a caller holding it is allowed to say
    # who the request is really from (see client_key in api/main.py).
    api_token: str | None = None

    # The website is the only intended caller. An empty list means no browser
    # origin is allowed, which is the right default for a service that would
    # otherwise answer anyone's page.
    allowed_origins: list[str] = ["http://localhost:3000"]

    # A conversation is held in memory, so both of these are really memory
    # limits as much as product decisions.
    session_ttl_minutes: int = 60
    max_sessions: int = 2000

    # Per conversation, then per client address. The second one is what stops
    # a single caller opening a thousand conversations to get around the first.
    messages_per_session_per_hour: int = 60
    messages_per_ip_per_hour: int = 120
    max_message_chars: int = 2000

    workbook_path: Path = PROJECT_ROOT / "data" / "raw" / "courses.xlsx"
    sheet_mapping_path: Path = PROJECT_ROOT / "data" / "mappings" / "sheet_columns.yaml"


@lru_cache
def get_settings() -> Settings:
    return Settings()
