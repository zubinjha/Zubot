"""Data-aware tool helpers built on kernel primitives."""

from .json_tools import read_json, write_json
from .text_search import search_text

__all__ = [
    "read_json",
    "search_text",
    "write_json",
]
