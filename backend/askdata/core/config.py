from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

class Settings(BaseSettings):
    LLM_API_BASE: str = "http://localhost:9001/v1"
    LLM_API_KEY: str = "sk-mock-key"
    LLM_MODEL_NAME: str = "Qwen3.5-397B-A17B"
    BIRD_DATA_DIR: str = "data/bird"
    BIRD_INSTRUCTIONS_DIR: str = "data/bird/instructions"
    APP_DATABASE_PATH: str = "data/askdata-app.sqlite"
    EMBEDDING_API_URL: str = ""
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_MODEL: str = "BAAI/bge-m3"
    EMBEDDING_DIMENSION: int = 1024
    EMBEDDING_TIMEOUT_SECONDS: float = 2.0
    MILVUS_URI: str = ""
    MILVUS_HOST: str = ""
    MILVUS_PORT: int = 19530
    MILVUS_COLLECTION: str = "askdata_schema_chunks"
    VECTOR_RETRIEVAL_ENABLED: bool = True

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def ResolvedMilvusUri(self) -> str:
        """Return the explicit Milvus URI or derive it from legacy host/port settings."""

        if self.MILVUS_URI:
            return self.MILVUS_URI
        if self.MILVUS_HOST:
            return f"http://{self.MILVUS_HOST}:{self.MILVUS_PORT}"
        return ""

settings = Settings()
