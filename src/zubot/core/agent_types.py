"""Core schemas for agent-loop orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

ModelTier = Literal["low", "medium", "high"]
WorkerStatus = Literal["success", "failed", "needs_user_input"]
SessionEventType = Literal[
    "user_message",
    "assistant_message",
    "tool_call",
    "tool_result",
    "worker_spawn",
    "worker_complete",
    "system",
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class TaskEnvelope:
    """Task payload for worker execution."""

    task_id: str
    requested_by: str
    instructions: str
    model_tier: ModelTier = "medium"
    tool_access: list[str] = field(default_factory=list)
    skill_access: list[str] = field(default_factory=list)
    deadline_iso: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("TaskEnvelope.task_id must be non-empty.")
        if not self.requested_by.strip():
            raise ValueError("TaskEnvelope.requested_by must be non-empty.")
        if not self.instructions.strip():
            raise ValueError("TaskEnvelope.instructions must be non-empty.")
        if self.model_tier not in {"low", "medium", "high"}:
            raise ValueError("TaskEnvelope.model_tier must be one of: low, medium, high.")

    @classmethod
    def create(
        cls,
        *,
        instructions: str,
        model_tier: ModelTier = "medium",
        requested_by: str = "main_agent",
        tool_access: list[str] | None = None,
        skill_access: list[str] | None = None,
        deadline_iso: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "TaskEnvelope":
        return cls(
            task_id=f"task_{uuid4().hex}",
            requested_by=requested_by,
            instructions=instructions,
            model_tier=model_tier,
            tool_access=tool_access or [],
            skill_access=skill_access or [],
            deadline_iso=deadline_iso,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "requested_by": self.requested_by,
            "instructions": self.instructions,
            "model_tier": self.model_tier,
            "tool_access": self.tool_access,
            "skill_access": self.skill_access,
            "deadline_iso": self.deadline_iso,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class WorkerResult:
    """Result returned by a worker after processing a task."""

    task_id: str
    status: WorkerStatus
    summary: str
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    trace: list[str] = field(default_factory=list)
    produced_at: str = field(default_factory=_utc_now_iso)

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("WorkerResult.task_id must be non-empty.")
        if self.status not in {"success", "failed", "needs_user_input"}:
            raise ValueError("WorkerResult.status is invalid.")
        if self.status == "failed" and not self.error:
            raise ValueError("WorkerResult.error is required when status='failed'.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "summary": self.summary,
            "artifacts": self.artifacts,
            "error": self.error,
            "trace": self.trace,
            "produced_at": self.produced_at,
        }


@dataclass(slots=True)
class SessionEvent:
    """Persistable event in a session timeline."""

    session_id: str
    event_type: SessionEventType
    payload: dict[str, Any]
    source: str = "main_agent"
    event_id: str = field(default_factory=lambda: f"evt_{uuid4().hex}")
    timestamp: str = field(default_factory=_utc_now_iso)

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise ValueError("SessionEvent.session_id must be non-empty.")
        if self.event_type not in {
            "user_message",
            "assistant_message",
            "tool_call",
            "tool_result",
            "worker_spawn",
            "worker_complete",
            "system",
        }:
            raise ValueError("SessionEvent.event_type is invalid.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "event_type": self.event_type,
            "payload": self.payload,
            "source": self.source,
            "timestamp": self.timestamp,
        }
