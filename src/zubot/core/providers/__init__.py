"""Provider adapters for LLM backends."""

from .openrouter import call_openrouter

__all__ = [
    "call_openrouter",
]
