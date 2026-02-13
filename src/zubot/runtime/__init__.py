"""Runtime facade for daemon/app integration."""

from .service import RuntimeService, get_runtime_service

__all__ = [
    "RuntimeService",
    "get_runtime_service",
]

