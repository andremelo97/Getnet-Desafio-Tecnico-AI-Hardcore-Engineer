"""The chat model shared by the four agents, built from settings with `init_chat_model`.

Temperature 0, a timeout and retries on every call. The provider is configuration
(`CHAT_MODEL=openai:gpt-4.1-mini`), and tests replace this model with a scripted fake.
"""

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from app.core.settings import Settings


def build_chat_model(settings: Settings) -> BaseChatModel:
    return init_chat_model(
        settings.chat_model,
        temperature=0,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        api_key=settings.openai_api_key.get_secret_value(),
    )
