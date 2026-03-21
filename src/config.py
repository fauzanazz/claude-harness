from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    model: str = "claude-sonnet-4-6"
    sandbox_image: str = "claude-harness-sandbox"
    sandbox_memory: str = "2g"
    sandbox_cpus: int = 1
    sandbox_timeout: int = 3600
    pool_min_size: int = 1
    pool_max_size: int = 5
    permission_timeout: int = 60
    max_context_tokens: int = 100_000
    compaction_model: str = "claude-haiku-4-5-20251001"


settings = Settings()
