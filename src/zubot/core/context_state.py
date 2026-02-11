"""Canonical context item state for agent turns."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

from .token_estimator import estimate_text_tokens


def fingerprint_text(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


@dataclass
class ContextItem:
    source_id: str
    content: str
    priority: str = "supplemental"
    pinned: bool = False
    token_estimate: int = 0
    fingerprint: str = ""
    last_used_turn: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.token_estimate <= 0:
            self.token_estimate = estimate_text_tokens(self.content)
        if not self.fingerprint:
            self.fingerprint = fingerprint_text(self.content)

    def to_prompt_message(self) -> dict[str, str]:
        label = self.metadata.get("label")
        if not isinstance(label, str) or not label.strip():
            label = self.source_id
        return {"role": "system", "content": f"[{label}]\n{self.content}"}


class ContextState:
    """Mutable registry of context items keyed by stable source id."""

    def __init__(self) -> None:
        self._items: dict[str, ContextItem] = {}

    def get(self, source_id: str) -> ContextItem | None:
        return self._items.get(source_id)

    def all_items(self) -> list[ContextItem]:
        return list(self._items.values())

    def remove(self, source_id: str) -> bool:
        return self._items.pop(source_id, None) is not None

    def touch(self, source_id: str, *, turn: int) -> bool:
        item = self._items.get(source_id)
        if item is None:
            return False
        item.last_used_turn = turn
        return True

    def upsert_item(
        self,
        source_id: str,
        content: str,
        *,
        priority: str = "supplemental",
        pinned: bool = False,
        turn: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(content, str):
            raise ValueError("Context content must be a string.")

        text_fingerprint = fingerprint_text(content)
        existing = self._items.get(source_id)
        created = existing is None
        changed = True

        if existing is not None and existing.fingerprint == text_fingerprint:
            changed = False
            existing.priority = priority
            existing.pinned = pinned
            existing.last_used_turn = turn if turn is not None else existing.last_used_turn
            if metadata is not None:
                existing.metadata = dict(metadata)
            item = existing
        else:
            item = ContextItem(
                source_id=source_id,
                content=content,
                priority=priority,
                pinned=pinned,
                token_estimate=estimate_text_tokens(content),
                fingerprint=text_fingerprint,
                last_used_turn=turn,
                metadata=dict(metadata or {}),
            )
            self._items[source_id] = item

        return {
            "created": created,
            "changed": changed,
            "source_id": source_id,
            "fingerprint": item.fingerprint,
            "token_estimate": item.token_estimate,
        }
