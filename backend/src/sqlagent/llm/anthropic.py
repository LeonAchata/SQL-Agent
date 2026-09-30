"""Claude implementation of the :class:`~sqlagent.llm.base.LLM` interface."""

import logging

import anthropic
import pydantic
from anthropic.types.beta import BetaOutputConfigParam, BetaTextBlockParam

from sqlagent.config import Effort
from sqlagent.llm.base import LLMError, LLMMessage, T

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that accept the server-side ``fallbacks: "default"`` parameter.
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}


class AnthropicLLM:
    def __init__(
        self,
        *,
        model: str = "claude-opus-5-5",
        effort: Effort = "medium",
        refusal_fallbacks: bool = True,
        max_tokens: int = 16_000,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        self.model = model
        self.effort: Effort = effort
        self.max_tokens = max_tokens
        self.fallbacks = refusal_fallbacks and model in FALLBACK_MODELS
        # Resolves ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / an `ant auth login` profile.
        self.client = client or anthropic.Anthropic()

    def structured(
        self,
        *,
        instructions: str,
        context: str,
        messages: list[LLMMessage],
        schema: type[T],
    ) -> T:
        system: list[BetaTextBlockParam] = [
            {"type": "text", "text": instructions},
            # Schema context is identical across requests for one database: cache it.
            {"type": "text", "text": context, "cache_control": {"type": "ephemeral"}},
        ]
        # Plain `create` + our own parsing (rather than `parse`) so a refusal or truncated
        # response is reported through stop_reason instead of a JSON validation error.
        output_config: BetaOutputConfigParam = {
            "effort": self.effort,
            "format": {"type": "json_schema", "schema": anthropic.transform_schema(schema)},
        }
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": m["role"], "content": m["content"]} for m in messages],
                output_config=output_config,
                betas=[FALLBACK_BETA] if self.fallbacks else anthropic.omit,
                fallbacks="default" if self.fallbacks else anthropic.omit,
            )
        except anthropic.RateLimitError as exc:
            raise LLMError("The model is rate limited; try again shortly.") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Model request failed ({exc.status_code}): {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Could not reach the model API.") from exc

        usage = response.usage
        log.debug(
            "LLM %s: in=%s cached=%s out=%s",
            schema.__name__,
            usage.input_tokens,
            usage.cache_read_input_tokens,
            usage.output_tokens,
        )
        if response.stop_reason == "refusal":
            raise LLMError("The model declined this request.")
        if response.stop_reason == "max_tokens":
            raise LLMError("The model response was cut off (max_tokens).")
        text = "".join(block.text for block in response.content if block.type == "text")
        try:
            return schema.model_validate_json(text)
        except pydantic.ValidationError as exc:
            raise LLMError("The model did not return the expected structure.") from exc
