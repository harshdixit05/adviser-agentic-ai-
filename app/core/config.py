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

    workbook_path: Path = PROJECT_ROOT / "data" / "raw" / "courses.xlsx"
    sheet_mapping_path: Path = PROJECT_ROOT / "data" / "mappings" / "sheet_columns.yaml"


@lru_cache
def get_settings() -> Settings:
    return Settings()
