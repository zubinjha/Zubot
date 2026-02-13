"""Predefined task resolution and execution for central scheduler runs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config_loader import load_config
from .sub_agent_runner import SubAgentRunner


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


class TaskAgentRunner:
    """Resolve and execute predefined task runs from config."""

    def __init__(self, *, runner: SubAgentRunner | None = None) -> None:
        # Reserved for future hybrid execution modes.
        self._runner = runner

    @staticmethod
    def _load_predefined_tasks() -> dict[str, dict[str, Any]]:
        try:
            cfg = load_config()
        except Exception:
            return {}
        root = cfg.get("pre_defined_tasks") if isinstance(cfg, dict) else None
        tasks = root.get("tasks") if isinstance(root, dict) else None
        if not isinstance(tasks, dict):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for task_id, payload in tasks.items():
            if isinstance(task_id, str) and isinstance(payload, dict):
                out[task_id] = payload
        return out

    @staticmethod
    def _resolve_predefined_entrypoint(entrypoint_path: str) -> Path:
        candidate = Path(entrypoint_path.strip())
        if candidate.is_absolute():
            raise ValueError("Predefined task entrypoint_path must be repository-relative.")
        normalized_parts = [part for part in candidate.parts if part not in ("", ".")]
        if any(part == ".." for part in normalized_parts):
            raise ValueError("Path traversal is not allowed in predefined task entrypoint_path.")
        resolved = (_repo_root() / candidate).resolve()
        if not resolved.exists() or not resolved.is_file():
            raise ValueError(f"Predefined task entrypoint file not found: {entrypoint_path}")
        return resolved

    @staticmethod
    def _run_predefined_task(*, task_id: str, task_def: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
        name = str(task_def.get("name") or task_id)
        entrypoint_raw = str(task_def.get("entrypoint_path") or "").strip()
        if not entrypoint_raw:
            return {
                "ok": False,
                "status": "failed",
                "summary": None,
                "error": f"Predefined task `{task_id}` is missing entrypoint_path.",
                "current_description": f"{name}: missing entrypoint_path.",
                "model_alias": "predefined",
                "used_tool_access": [],
                "used_skill_access": [],
                "retryable_error": False,
                "attempts_used": None,
                "attempts_configured": None,
            }

        try:
            entrypoint = TaskAgentRunner._resolve_predefined_entrypoint(entrypoint_raw)
        except ValueError as exc:
            return {
                "ok": False,
                "status": "failed",
                "summary": None,
                "error": str(exc),
                "current_description": f"{name}: invalid entrypoint.",
                "model_alias": "predefined",
                "used_tool_access": [],
                "used_skill_access": [],
                "retryable_error": False,
                "attempts_used": None,
                "attempts_configured": None,
            }

        args = task_def.get("args")
        arg_list = [str(item) for item in args if isinstance(item, (str, int, float))] if isinstance(args, list) else []
        timeout_raw = task_def.get("timeout_sec")
        timeout_sec = int(timeout_raw) if isinstance(timeout_raw, int) and timeout_raw > 0 else 1800

        env = os.environ.copy()
        env["ZUBOT_TASK_ID"] = task_id
        env["ZUBOT_TASK_PAYLOAD_JSON"] = json.dumps(payload if isinstance(payload, dict) else {})

        try:
            completed = subprocess.run(
                [sys.executable, str(entrypoint), *arg_list],
                cwd=str(_repo_root()),
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "status": "failed",
                "summary": None,
                "error": f"Predefined task `{task_id}` timed out after {timeout_sec}s.",
                "current_description": f"{name}: timed out after {timeout_sec}s.",
                "model_alias": "predefined",
                "used_tool_access": [],
                "used_skill_access": [],
                "retryable_error": False,
                "attempts_used": None,
                "attempts_configured": None,
            }
        except Exception as exc:
            return {
                "ok": False,
                "status": "failed",
                "summary": None,
                "error": f"Predefined task `{task_id}` failed to start: {exc}",
                "current_description": f"{name}: failed to start.",
                "model_alias": "predefined",
                "used_tool_access": [],
                "used_skill_access": [],
                "retryable_error": False,
                "attempts_used": None,
                "attempts_configured": None,
            }

        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        if completed.returncode == 0:
            summary = stdout.splitlines()[-1][:300] if stdout else f"{name} completed."
            return {
                "ok": True,
                "status": "done",
                "summary": summary,
                "error": None,
                "current_description": f"{name}: completed.",
                "model_alias": "predefined",
                "used_tool_access": [],
                "used_skill_access": [],
                "retryable_error": False,
                "attempts_used": 1,
                "attempts_configured": 1,
            }

        err_msg = stderr[:500] if stderr else f"exit_code={completed.returncode}"
        return {
            "ok": False,
            "status": "failed",
            "summary": stdout.splitlines()[-1][:300] if stdout else None,
            "error": f"Predefined task `{task_id}` failed: {err_msg}",
            "current_description": f"{name}: failed (exit {completed.returncode}).",
            "model_alias": "predefined",
            "used_tool_access": [],
            "used_skill_access": [],
            "retryable_error": False,
            "attempts_used": 1,
            "attempts_configured": 1,
        }

    def describe_run(self, *, profile_id: str, payload: dict[str, Any] | None = None) -> str:
        _ = payload
        predefined = self._load_predefined_tasks().get(profile_id)
        if not isinstance(predefined, dict):
            return f"Predefined task `{profile_id}` is not defined."

        task_name = str(predefined.get("name") or profile_id)
        entrypoint = str(predefined.get("entrypoint_path") or "").strip()
        if entrypoint:
            return f"{task_name}: executing `{entrypoint}`."
        return f"{task_name}: predefined task execution."

    def run_profile(self, *, profile_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        predefined = self._load_predefined_tasks().get(profile_id)
        if not isinstance(predefined, dict):
            return {
                "ok": False,
                "status": "failed",
                "summary": None,
                "error": f"Predefined task `{profile_id}` not found.",
                "current_description": f"Failed to resolve predefined task `{profile_id}`.",
                "model_alias": "predefined",
                "used_tool_access": [],
                "used_skill_access": [],
                "retryable_error": False,
                "attempts_used": None,
                "attempts_configured": None,
            }

        return self._run_predefined_task(task_id=profile_id, task_def=predefined, payload=payload)
