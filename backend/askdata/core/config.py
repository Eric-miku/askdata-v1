from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

class Settings(BaseSettings):
    LLM_API_BASE: str = "http://localhost:9001/v1"
    LLM_API_KEY: str = ""
    LLM_MODEL_NAME: str = "local-model"
    LLM_THINKING_ENABLED: bool = False
    LLM_REASONING_EFFORT: str = "high"
    BIRD_DATA_DIR: str = "data/bird"
    BIRD_INSTRUCTIONS_DIR: str = "data/bird/instructions"
    ADMIN_API_TOKEN: str = ""
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"
    QUERY_MAX_ROWS: int = 1000
    EXPORT_MAX_ROWS: int = 10000
    MAX_RESULT_BYTES: int = 10 * 1024 * 1024
    SQL_MAX_JOINS: int = 8
    SQL_MAX_SUBQUERY_DEPTH: int = 4
    SQL_STATEMENT_TIMEOUT_SECONDS: float = 15.0
    SLOW_QUERY_MS: float = 2000.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
