"""Configuration is local to this project; no credentials are required."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SANYOJAN_", extra="ignore")

    database_path: Path = Path("data/tasks.sqlite3")
    reports_dir: Path = Path("data/reports")
    headless: bool = True
    task_timeout_seconds: int = Field(60, ge=5, le=180)
    action_timeout_ms: int = Field(15000, ge=1000, le=30000)
    max_page_bytes: int = Field(2_000_000, ge=1024, le=5_000_000)
    max_records: int = Field(100, ge=1, le=200)
    max_redirects: int = Field(3, ge=0, le=5)
    max_concurrent_tasks: int = Field(2, ge=1, le=4)
    max_stored_tasks: int = Field(1000, ge=10, le=10000)
    allowed_domains: list[str] = ["books.toscrape.com"]
    cors_origins: list[str] = ["http://127.0.0.1:5173", "http://localhost:5173"]
    trusted_hosts: list[str] = ["127.0.0.1", "localhost", "testserver"]
    frontend_dir: Path = Path("frontend/dist")
