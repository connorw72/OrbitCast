"""API settings (CLAUDE.md §7). Locations enter as H3 cells only (D12)."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ORBITCAST_")

    # Root of the gitignored data tree; the CelesTrak cache lives under it.
    data_dir: Path = Path("data")

    @property
    def celestrak_dir(self) -> Path:
        return self.data_dir / "raw" / "celestrak"


@lru_cache
def get_settings() -> Settings:
    return Settings()
