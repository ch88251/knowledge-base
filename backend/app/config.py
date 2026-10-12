from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KB_", env_file=".env", extra="ignore")

    elasticsearch_url: str = "http://localhost:9200"
    index_name: str = "knowledge-base"
    google_credentials_file: Path = Path("credentials.json")
    google_token_file: Path = Path("token.json")
    drive_folder_id: str | None = None
    # Fixed so the OAuth redirect can be published from a container.
    oauth_port: int = 8090
    # Personal access token; GitHub is synced only when this is set.
    github_token: str | None = None
    github_include_forks: bool = False
    max_file_bytes: int = 50 * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
