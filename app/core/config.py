from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env.local", extra="ignore"
    )

    # A file, so trying this out needs no database server. The schema is
    # dialect-agnostic; point this at Postgres for anything deployed.
    #
    # as_posix() because a Windows path lands here as C:\...\advisor.db, and
    # the backslashes are not safe to paste into a URL.
    database_url: str = f"sqlite:///{(PROJECT_ROOT / 'data' / 'advisor.db').as_posix()}"

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

    def resolve_workbook(self) -> Path:
        """
        The workbook, under whatever name it arrived with.

        Windows hides known extensions, so renaming a download to
        "courses.xlsx" in Explorer commonly yields courses.xlsx.xlsx. Rather
        than fail on a name, accept a single spreadsheet sitting in the folder.
        Ambiguity is still an error: picking one of several silently would be a
        catalogue built from the wrong file.
        """
        if self.workbook_path.exists():
            return self.workbook_path

        folder = self.workbook_path.parent
        found = sorted(p for p in folder.glob("*.xlsx") if not p.name.startswith("~$"))
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            names = ", ".join(p.name for p in found)
            raise FileNotFoundError(
                f"More than one spreadsheet in {folder}: {names}. "
                "Leave only the course workbook there, or name it courses.xlsx."
            )
        raise FileNotFoundError(
            f"No course workbook found in {folder}. It is deliberately not in "
            "git — copy the .xlsx into that folder."
        )
    sheet_mapping_path: Path = PROJECT_ROOT / "data" / "mappings" / "sheet_columns.yaml"


@lru_cache
def get_settings() -> Settings:
    return Settings()
