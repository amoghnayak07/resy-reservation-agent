from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    cors_origins: str = "http://localhost:5173"
    app_timezone: str = "America/New_York"
    render_git_commit: str = "dev"

    openai_api_key: str
    openai_model: str = "gpt-6-sol"

    database_url: str

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def database_url_psycopg(self) -> str:
        """Plain postgresql:// DSN for raw psycopg connections (e.g. the LangGraph checkpointer)."""
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)


settings = Settings()  # pyright: ignore[reportCallIssue] -- required fields load from the environment
