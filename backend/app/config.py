from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    database_url: str = 'sqlite:///./trajnexa_dev.sqlite3'
    storage_root: Path = Path('./storage')
    max_upload_bytes: int = 268435456
    worker_poll_seconds: float = 2

settings = Settings()
