from pathlib import Path
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    jarvis_api_key: SecretStr
    token_encryption_key: SecretStr
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"
    jarvis_memory_id: str = ""
    jarvis_memory_tab_id: str = ""
    google_calendar_id: str = "primary"
    google_tasklist_id: str = "@default"
    timezone: str = "Europe/Moscow"
    data_dir: Path = Path("data")
    confirmation_ttl_seconds: int = Field(300, ge=30, le=3600)
    jules_api_key: SecretStr = SecretStr("")
    jules_source: str = ""
    jules_branch: str = "main"
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = ""

    @model_validator(mode="after")
    def check(self):
        if len(self.jarvis_api_key.get_secret_value()) < 32:
            raise ValueError("JARVIS_API_KEY must contain at least 32 characters")
        Fernet(self.token_encryption_key.get_secret_value().encode())
        ZoneInfo(self.timezone)
        if not (
            self.google_redirect_uri.startswith("https://")
            or self.google_redirect_uri.startswith("http://localhost:")
        ):
            raise ValueError("OAuth redirect must use HTTPS or localhost")
        return self
