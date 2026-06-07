from pathlib import Path
import os
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    # Browser Settings
    HEADLESS: bool = True
    TRACES_URL: str = "https://traces.tdscpc.gov.in"
    TIMEOUT: int = 30000
    VIEWPORT_WIDTH: int = 1280
    VIEWPORT_HEIGHT: int = 720

    # Session Settings
    SESSION_TTL: int = 1800  # 30 minutes
    SESSION_CLEANUP_INTERVAL: int = 300  # 5 minutes

    # Page Pool Settings
    MAX_PAGES_PER_SESSION: int = 3

    # Selector Settings
    SELECTORS_PATH: str = str(BASE_DIR / "config" / "selectors.json")

    # Logging Settings
    LOG_LEVEL: str = "INFO"
    DEBUG_MODE: bool = False

    # Error Handling
    RETRY_ATTEMPTS: int = 3
    RETRY_WAIT_TIME: int = 2

    class Config:
        env_file = str(BASE_DIR.parent / ".env")

settings = Settings()
