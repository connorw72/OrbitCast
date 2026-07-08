"""API settings (CLAUDE.md §7). Locations enter as H3 cells only (D12)."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ORBITCAST_")

    # Root of the gitignored data tree; the CelesTrak cache lives under it.
    data_dir: Path = Path("data")

    # Serving-store DSN (§7.2). Read from the unprefixed DATABASE_URL that
    # docker-compose sets; defaults to the local compose Postgres for host runs.
    database_url: str = Field(
        default="postgresql://orbitcast:orbitcast@localhost:5432/orbitcast",
        validation_alias="DATABASE_URL",
    )

    @property
    def celestrak_dir(self) -> Path:
        return self.data_dir / "raw" / "celestrak"

    @property
    def models_dir(self) -> Path:
        """Promoted LightGBM artifacts volume (written by train_models)."""
        return self.data_dir / "models"

    @property
    def marts_dir(self) -> Path:
        """DuckDB Parquet marts (Ookla context, label aggregates)."""
        return self.data_dir / "marts"


@lru_cache
def get_settings() -> Settings:
    return Settings()
