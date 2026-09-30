"""Provider-agnostic interface the agent talks to. Implement it to plug in another model."""

from typing import Literal, Protocol, TypedDict, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMMessage(TypedDict):
    role: Literal["user", "assistant"]
    content: str


class LLMError(RuntimeError):
    """The model did not return a usable structured response."""


class LLM(Protocol):
    def structured(
        self,
        *,
        instructions: str,
        context: str,
        messages: list[LLMMessage],
        schema: type[T],
    ) -> T:
        """Return an instance of ``schema``.

        ``instructions`` and ``context`` are stable across requests for the same database, so
        implementations should cache them; ``messages`` carry the per-request content.
        """
        ...
