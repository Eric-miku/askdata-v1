from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

class Settings(BaseSettings):
    LLM_API_BASE: str = "http://localhost:9001/v1"
    LLM_API_KEY: str = "sk-mock-key"
    LLM_MODEL_NAME: str = "Qwen3.5-397B-A17B"
    BIRD_DATA_DIR: str = "data/bird"
    BIRD_INSTRUCTIONS_DIR: str = "data/bird/instructions"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
