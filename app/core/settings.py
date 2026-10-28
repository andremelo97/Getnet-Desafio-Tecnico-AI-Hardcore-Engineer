"""Configuration from the environment, validated once at startup.

Every tunable of the system lives here with its default. Required keys fail fast, so a
missing secret is a startup error, never a confusing failure on the first request.
"""

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: SecretStr
    tavily_api_key: SecretStr

    chat_model: str = "openai:gpt-4.1-mini"
    embedding_model: str = "text-embedding-3-small"
    llm_timeout_seconds: float = 30
    llm_max_retries: int = 2
    tool_call_limit: int = 6

    sources_file: Path = PROJECT_ROOT / "data" / "sources.json"
    customers_file: Path = PROJECT_ROOT / "data" / "customers.json"
    vectorstore_dir: Path = PROJECT_ROOT / ".vectorstore"
    collection_name: str = "getnet_kb"

    chunk_size: int = 1200
    chunk_overlap: int = 200
    retrieval_k: int = 4
    retrieval_min_score: float = 0.2
    web_search_max_results: int = 3

    timezone: str = "America/Sao_Paulo"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    # LangSmith reads LANGSMITH_* from os.environ, not from Settings: export .env for it too.
    # Variables already set (by Docker's env_file or the shell) win over the file.
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    return Settings()  # pyright: ignore[reportCallIssue]  # required keys come from the env
