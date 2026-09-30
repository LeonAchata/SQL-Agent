from sqlagent.config import Settings
from sqlagent.llm.base import LLM, LLMError, LLMMessage


def create_llm(settings: Settings) -> LLM:
    if settings.llm_provider == "anthropic":
        from sqlagent.llm.anthropic import AnthropicLLM

        return AnthropicLLM(
            model=settings.model,
            effort=settings.effort,
            refusal_fallbacks=settings.refusal_fallbacks,
        )
    raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")


__all__ = ["LLM", "LLMError", "LLMMessage", "create_llm"]
