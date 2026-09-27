from decimal import Decimal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    cors_origins: str = "http://localhost:5173"
    render_git_commit: str = "dev"
    search_radius_km: float = 40.0

    openai_api_key: str
    openai_model: str = "gpt-6-sol"

    database_url: str

    langfuse_public_key: str
    langfuse_secret_key: str
    langfuse_host: str = "https://us.cloud.langfuse.com"

    daily_spend_cap_usd: Decimal = Decimal("2.00")
    rate_limit_session_per_min: int = 10
    rate_limit_ip_per_hour: int = 60
    rate_limit_ip_per_day: int = 200

    max_message_chars: int = 1000
    max_turns_per_conversation: int = 30

    # Personal-account tokens, rotated by hand (no auto-refresh). Empty defaults keep CI and
    # the migrate job working without them; the Resy client refuses to send a request unset.
    resy_api_key: str = ""
    resy_auth_token: str = ""
    resy_writes_enabled: bool = False

    # Required to confirm a booking. Empty means every confirm is refused.
    demo_booking_passcode: SecretStr = SecretStr("")

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def database_url_psycopg(self) -> str:
        """Plain postgresql:// DSN for raw psycopg connections (e.g. the LangGraph checkpointer)."""
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)


settings = Settings()  # pyright: ignore[reportCallIssue] -- required fields load from the environment
