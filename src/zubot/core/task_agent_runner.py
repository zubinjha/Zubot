"""Task-agent profile resolution and run execution."""

from __future__ import annotations

from uuid import uuid4
from typing import Any

from .agent_types import TaskEnvelope
from .config_loader import get_model_config, load_config
from .context_loader import load_base_context
from .daily_memory import load_recent_daily_memory
from .sub_agent_runner import SubAgentRunner

TASK_AGENT_BASE_CONTEXT_FILES = [
    "context/KERNEL.md",
    "context/TASK_AGENT.md",
    "context/TASK_SOUL.md",
    "context/USER.md",
]


class TaskAgentRunner:
    """Resolve and execute task-agent profile runs.

    v1 behavior is profile-driven and returns structured summaries.
    Execution intentionally avoids side effects beyond run-state updates until
    dedicated task handlers are introduced.
    """

    def __init__(self, *, runner: SubAgentRunner | None = None) -> None:
        self._runner = runner or SubAgentRunner()

    @staticmethod
    def _load_profiles() -> dict[str, dict[str, Any]]:
        try:
            cfg = load_config()
        except Exception:
            return {}
        task_agents = cfg.get("task_agents") if isinstance(cfg, dict) else None
        profiles = task_agents.get("profiles") if isinstance(task_agents, dict) else None
        if not isinstance(profiles, dict):
            return {}

        out: dict[str, dict[str, Any]] = {}
        for profile_id, payload in profiles.items():
            if not isinstance(profile_id, str) or not isinstance(payload, dict):
                continue
            out[profile_id] = payload
        return out

    @staticmethod
    def _memory_autoload_days() -> int:
        try:
            cfg = load_config()
        except Exception:
            return 2
        memory_cfg = cfg.get("memory") if isinstance(cfg, dict) else None
        value = memory_cfg.get("autoload_summary_days") if isinstance(memory_cfg, dict) else None
        if isinstance(value, int) and value > 0:
            return value
        return 2

    @staticmethod
    def _normalize_model_tier(model_alias: str) -> str:
        alias = model_alias.strip().lower()
        if alias in {"low", "medium", "high"}:
            return alias
        return "medium"

    @staticmethod
    def _instructions_for_run(*, profile_name: str, profile: dict[str, Any], payload: dict[str, Any] | None = None) -> str:
        template = str(profile.get("instructions_template") or "").strip()
        payload_dict = payload if isinstance(payload, dict) else {}
        description = str(payload_dict.get("description") or "").strip()
        trigger = str(payload_dict.get("trigger") or "scheduled")
        run_context = f"Run context:\n- trigger: {trigger}"
        if description:
            run_context += f"\n- description: {description}"
        policy_hint = (
            "Worker escalation policy:\n"
            "- If you need to spawn a worker from this task-agent run, use `spawn_task_agent_worker`.\n"
            "- Do not call `spawn_worker` directly from task-agent runs."
        )

        if template:
            return f"{template}\n\n{run_context}\n\n{policy_hint}".strip()
        return f"Execute task-agent profile `{profile_name}`.\n\n{run_context}\n\n{policy_hint}"

    @staticmethod
    def _normalize_tool_access(profile_tools: list[str]) -> list[str]:
        """Normalize task-agent tool access to reserve-aware orchestration tools."""
        out: list[str] = []
        for tool_name in profile_tools:
            if tool_name == "spawn_worker":
                if "spawn_task_agent_worker" not in out:
                    out.append("spawn_task_agent_worker")
                continue
            if tool_name not in out:
                out.append(tool_name)
        return out

    @staticmethod
    def _extract_llm_failure_meta(result: dict[str, Any]) -> dict[str, Any]:
        artifacts = result.get("artifacts")
        if not isinstance(artifacts, list):
            return {}
        for item in artifacts:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "llm_failure":
                continue
            data = item.get("data")
            if isinstance(data, dict):
                return data
        return {}

    def _resolve_model_alias(self, profile: dict[str, Any]) -> tuple[bool, str, str | None]:
        model_alias = str(profile.get("model_alias") or "medium").strip() or "medium"
        try:
            # Validates alias/id existence in runtime config.
            get_model_config(model_alias)
        except Exception as exc:
            return False, model_alias, str(exc)
        return True, model_alias, None

    def _load_context(self, profile: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
        base_context = load_base_context(files=TASK_AGENT_BASE_CONTEXT_FILES)
        preload_files = profile.get("preload_files")
        preload_list = [path for path in preload_files if isinstance(path, str)] if isinstance(preload_files, list) else []
        supplemental_context = load_base_context(files=preload_list) if preload_list else {}
        supplemental_context.update(load_recent_daily_memory(days=self._memory_autoload_days()))
        return base_context, supplemental_context

    def describe_run(self, *, profile_id: str, payload: dict[str, Any] | None = None) -> str:
        profiles = self._load_profiles()
        profile = profiles.get(profile_id)
        if not isinstance(profile, dict):
            return f"Task profile `{profile_id}` is not defined."

        profile_name = str(profile.get("name") or profile_id)
        instructions_template = str(profile.get("instructions_template") or "").strip()
        if instructions_template:
            text = instructions_template.replace("\n", " ").strip()
            if len(text) > 160:
                text = text[:157] + "..."
            return f"{profile_name}: {text}"
        return f"{profile_name}: processing scheduled task run."

    def run_profile(self, *, profile_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        profiles = self._load_profiles()
        profile = profiles.get(profile_id)
        if not isinstance(profile, dict):
            return {
                "ok": False,
                "status": "failed",
                "summary": None,
                "error": f"Task profile `{profile_id}` not found.",
                "current_description": f"Failed to resolve task profile `{profile_id}`.",
            }

        profile_name = str(profile.get("name") or profile_id)
        model_ok, model_alias, model_error = self._resolve_model_alias(profile)
        if not model_ok:
            return {
                "ok": False,
                "status": "failed",
                "summary": None,
                "error": f"Task profile `{profile_id}` has invalid model alias `{model_alias}`: {model_error}",
                "current_description": f"Failed to resolve model alias `{model_alias}` for task profile `{profile_id}`.",
            }

        tool_access = profile.get("tool_access")
        skill_access = profile.get("skill_access")
        raw_tool_list = [name for name in tool_access if isinstance(name, str)] if isinstance(tool_access, list) else []
        tool_list = self._normalize_tool_access(raw_tool_list)
        skill_list = [name for name in skill_access if isinstance(name, str)] if isinstance(skill_access, list) else []
        base_context, supplemental_context = self._load_context(profile)

        instructions = self._instructions_for_run(profile_name=profile_name, profile=profile, payload=payload)
        task = TaskEnvelope(
            task_id=f"task_agent_{uuid4().hex}",
            requested_by=f"task_agent:{profile_id}",
            instructions=instructions,
            model_tier=self._normalize_model_tier(model_alias),  # type: ignore[arg-type]
            tool_access=tool_list,
            skill_access=skill_list,
            metadata={
                "profile_id": profile_id,
                "profile_name": profile_name,
                "trigger": (payload or {}).get("trigger", "scheduled"),
                "payload": payload if isinstance(payload, dict) else {},
            },
        )

        run_out = self._runner.run_task(
            task,
            model=model_alias,
            base_context=base_context,
            supplemental_context=supplemental_context,
            allow_orchestration_tools=True,
        )

        result = run_out.get("result") if isinstance(run_out.get("result"), dict) else {}
        result_status = str(result.get("status") or "").strip().lower()
        if run_out.get("ok") is True:
            if result_status == "needs_user_input":
                status = "blocked"
            elif result_status == "failed":
                status = "failed"
            else:
                status = "done"
        else:
            status = "failed"

        summary = result.get("summary") if isinstance(result.get("summary"), str) else None
        error = result.get("error") if isinstance(result.get("error"), str) else None
        llm_failure = self._extract_llm_failure_meta(result)
        attempts_used = llm_failure.get("attempts_used") if isinstance(llm_failure.get("attempts_used"), int) else None
        attempts_configured = (
            llm_failure.get("attempts_configured")
            if isinstance(llm_failure.get("attempts_configured"), int)
            else None
        )
        retryable_error = bool(llm_failure.get("retryable_error", False))
        if summary is None:
            summary = f"{profile_name} run completed (model={model_alias})." if status == "done" else None
        if error is None and run_out.get("ok") is not True:
            error = str(run_out.get("error") or "task_agent_run_failed")

        desc = self.describe_run(profile_id=profile_id, payload=payload)
        return {
            "ok": status == "done",
            "status": status,
            "summary": summary,
            "error": error,
            "current_description": desc,
            "model_alias": model_alias,
            "used_tool_access": tool_list,
            "used_skill_access": skill_list,
            "retryable_error": retryable_error,
            "attempts_used": attempts_used,
            "attempts_configured": attempts_configured,
        }
