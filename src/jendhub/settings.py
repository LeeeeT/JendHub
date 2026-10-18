from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LibrarySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JENDHUB_")

    library_path: Path = Path("data/library")


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JENDHUB_")

    database_url: str
    database_pool_size: int = 10


class QdrantSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JENDHUB_")

    qdrant_url: str
    qdrant_api_key: SecretStr | None = None


class OpenRouterSettings(BaseSettings):
    openrouter_api_key: SecretStr = Field(validation_alias="OPENROUTER_API_KEY")


def load_settings[T: BaseSettings](kind: type[T]) -> T:
    return kind()  # pyright: ignore[reportCallIssue]
