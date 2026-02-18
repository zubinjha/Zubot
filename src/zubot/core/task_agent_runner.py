"""Predefined task resolution and execution for central scheduler runs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from threading import Event
from time import monotonic, sleep
from pathlib import Path
from typing import Any

from .config_loader import get_task_profiles_config, load_config
from .sub_agent_runner import SubAgentRunner


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


class TaskAgentRunner:
    """Resolve and execute predefined task runs from config."""

    def __init__(self, *, runner: SubAgentRunner | None = None) -> None:
        self._runner = runner or SubAgentRunner()

    @staticmethod
    def _load_task_profiles() -> dict[str, dict[str, Any]]:
        try:
            cfg = load_config()
        except Exception:
            return {}
        task_cfg = get_task_profiles_config(cfg if isinstance(cfg, dict) else None)
        tasks = task_cfg.get("tasks")
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
    def _resolve_task_resources_dir(*, task_id: str, task_def: dict[str, Any]) -> Path:
        candidate_raw = task_def.get("resources_path")
        if isinstance(candidate_raw, str) and candidate_raw.strip():
            candidate = Path(candidate_raw.strip())
        else:
            candidate = Path("src") / "zubot" / "predefined_tasks" / task_id

        if candidate.is_absolute():
            raise ValueError("Task resources_path must be repository-relative.")
        normalized_parts = [part for part in candidate.parts if part not in ("", ".")]
        if any(part == ".." for part in normalized_parts):
            raise ValueError("Path traversal is not allowed in task resources_path.")
        return (_repo_root() / candidate).resolve()

    @staticmethod
    def _load_task_local_config(resources_dir: Path) -> dict[str, Any]:
        for filename in ("task_config.json", "config.json"):
            config_path = resources_dir / filename
            if not config_path.exists() or not config_path.is_file():
                continue
            try:
                payload = json.loads(config_path.read_text(encoding="utf-8"))
            except Exception:
                return {}
            return payload if isinstance(payload, dict) else {}
        return {}

    @staticmethod
    def _run_predefined_task(
        *,
        task_id: str,
        task_def: dict[str, Any],
        payload: dict[str, Any] | None = None,
        cancel_event: Event | None = None,
    ) -> dict[str, Any]:
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
        try:
            resources_dir = TaskAgentRunner._resolve_task_resources_dir(task_id=task_id, task_def=task_def)
        except ValueError as exc:
            return {
                "ok": False,
                "status": "failed",
                "summary": None,
                "error": str(exc),
                "current_description": f"{name}: invalid resources_path.",
                "model_alias": "predefined",
                "used_tool_access": [],
                "used_skill_access": [],
                "retryable_error": False,
                "attempts_used": None,
                "attempts_configured": None,
            }
        task_local_config = TaskAgentRunner._load_task_local_config(resources_dir)
        timeout_raw = task_def.get("timeout_sec")
        timeout_sec = int(timeout_raw) if isinstance(timeout_raw, int) and timeout_raw > 0 else None
        if timeout_sec is None:
            local_timeout = task_local_config.get("task_timeout_sec")
            timeout_sec = int(local_timeout) if isinstance(local_timeout, int) and local_timeout > 0 else 1800

        env = os.environ.copy()
        env["ZUBOT_TASK_ID"] = task_id
        env["ZUBOT_TASK_PAYLOAD_JSON"] = json.dumps(payload if isinstance(payload, dict) else {})
        env["ZUBOT_TASK_RESOURCES_DIR"] = str(resources_dir)
        env["ZUBOT_TASK_LOCAL_CONFIG_JSON"] = json.dumps(task_local_config)
        env["ZUBOT_TASK_PROFILE_JSON"] = json.dumps(task_def)
        stream_output = str(env.get("ZUBOT_TASK_STREAM_STDOUT", "")).strip().lower() in {"1", "true", "yes", "on"}

        try:
            process = subprocess.Popen(
                [sys.executable, str(entrypoint), *arg_list],
                cwd=str(_repo_root()),
                stdout=None if stream_output else subprocess.PIPE,
                stderr=None if stream_output else subprocess.PIPE,
                text=True,
                env=env,
            )
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

        started = monotonic()
        timed_out = False
        cancelled = False
        while True:
            ret = process.poll()
            if ret is not None:
                break
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                break
            if monotonic() - started > timeout_sec:
                timed_out = True
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                break
            sleep(0.15)

        stdout, stderr = process.communicate()
        if not isinstance(stdout, str):
            stdout = ""
        if not isinstance(stderr, str):
            stderr = ""
        completed = subprocess.CompletedProcess(
            args=[sys.executable, str(entrypoint), *arg_list],
            returncode=int(process.returncode or 0),
            stdout=stdout,
            stderr=stderr,
        )

        if cancelled:
            return {
                "ok": False,
                "status": "blocked",
                "summary": None,
                "error": f"Predefined task `{task_id}` was killed by user request.",
                "current_description": f"{name}: killed by user.",
                "model_alias": "predefined",
                "used_tool_access": [],
                "used_skill_access": [],
                "retryable_error": False,
                "attempts_used": 1,
                "attempts_configured": 1,
            }

        if timed_out:
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

    def describe_run(
        self,
        *,
        profile_id: str,
        payload: dict[str, Any] | None = None,
        profile: dict[str, Any] | None = None,
    ) -> str:
        payload_dict = payload if isinstance(payload, dict) else {}
        run_kind = str(payload_dict.get("run_kind") or "predefined").strip().lower()
        if run_kind == "agentic":
            task_name = str(payload_dict.get("task_name") or "Agentic Task").strip() or "Agentic Task"
            instructions = str(payload_dict.get("instructions") or "").strip()
            preview = instructions[:72] + ("..." if len(instructions) > 72 else "")
            if preview:
                return f"{task_name}: agentic execution ({preview})"
            return f"{task_name}: agentic execution"

        profile = profile if isinstance(profile, dict) else self._load_task_profiles().get(profile_id)
        if not isinstance(profile, dict):
            return f"Task profile `{profile_id}` is not defined."

        task_name = str(profile.get("name") or profile_id)
        kind = str(profile.get("kind") or "script").strip().lower()
        entrypoint = str(profile.get("entrypoint_path") or "").strip()
        if kind == "interactive_wrapper":
            return f"{task_name}: interactive wrapper execution."
        if kind == "agentic":
            return f"{task_name}: agentic profile execution."
        if entrypoint:
            return f"{task_name}: executing `{entrypoint}`."
        return f"{task_name}: script profile execution."

    def _run_agentic_task(
        self,
        *,
        payload: dict[str, Any] | None = None,
        cancel_event: Event | None = None,
    ) -> dict[str, Any]:
        payload_dict = payload if isinstance(payload, dict) else {}
        task_name = str(payload_dict.get("task_name") or "Agentic Task").strip() or "Agentic Task"
        instructions = str(payload_dict.get("instructions") or "").strip()
        if not instructions:
            return {
                "ok": False,
                "status": "failed",
                "summary": None,
                "error": "Agentic task is missing instructions.",
                "current_description": f"{task_name}: missing instructions.",
                "model_alias": "medium",
                "used_tool_access": [],
                "used_skill_access": [],
                "retryable_error": False,
                "attempts_used": None,
                "attempts_configured": None,
            }

        if cancel_event is not None and cancel_event.is_set():
            return {
                "ok": False,
                "status": "blocked",
                "summary": None,
                "error": "Agentic task cancelled before start.",
                "current_description": f"{task_name}: cancelled before start.",
                "model_alias": "medium",
                "used_tool_access": [],
                "used_skill_access": [],
                "retryable_error": False,
                "attempts_used": 0,
                "attempts_configured": 1,
            }

        model_tier = str(payload_dict.get("model_tier") or "medium").strip().lower() or "medium"
        tool_access = [str(item).strip() for item in payload_dict.get("tool_access", []) if isinstance(item, str)]
        skill_access = [str(item).strip() for item in payload_dict.get("skill_access", []) if isinstance(item, str)]
        timeout_raw = payload_dict.get("timeout_sec")
        timeout_sec = int(timeout_raw) if isinstance(timeout_raw, int) and timeout_raw > 0 else 120
        max_steps_raw = payload_dict.get("max_steps")
        max_steps = int(max_steps_raw) if isinstance(max_steps_raw, int) and max_steps_raw > 0 else 4
        max_tool_calls_raw = payload_dict.get("max_tool_calls")
        max_tool_calls = int(max_tool_calls_raw) if isinstance(max_tool_calls_raw, int) and max_tool_calls_raw > 0 else 3
        requested_by = str(payload_dict.get("requested_by") or "main_agent").strip() or "main_agent"

        out = self._runner.run_task(
            {
                "task_id": str(payload_dict.get("task_id") or "agentic_task"),
                "requested_by": requested_by,
                "instructions": instructions,
                "model_tier": model_tier,
                "tool_access": tool_access,
                "skill_access": skill_access,
                "metadata": payload_dict.get("metadata") if isinstance(payload_dict.get("metadata"), dict) else {},
            },
            model=model_tier,
            max_steps=max_steps,
            max_tool_calls=max_tool_calls,
            timeout_sec=float(timeout_sec),
            allow_orchestration_tools=False,
        )
        result = out.get("result") if isinstance(out.get("result"), dict) else {}
        summary = str(result.get("summary") or "").strip() or None
        error = str(result.get("error") or "").strip() or None
        sub_status = str(result.get("status") or "").strip().lower()
        wait_context = result.get("wait_context") if isinstance(result.get("wait_context"), dict) else {}
        wait_timeout_sec = result.get("wait_timeout_sec") if isinstance(result.get("wait_timeout_sec"), int) else None

        if cancel_event is not None and cancel_event.is_set():
            return {
                "ok": False,
                "status": "blocked",
                "summary": summary,
                "error": "Agentic task was killed by user request.",
                "current_description": f"{task_name}: killed by user.",
                "model_alias": model_tier,
                "used_tool_access": tool_access,
                "used_skill_access": skill_access,
                "retryable_error": False,
                "attempts_used": 1,
                "attempts_configured": 1,
            }

        if out.get("ok") and sub_status in {"success", "needs_user_input"}:
            terminal_status = "done" if sub_status == "success" else "waiting_for_user"
            question = summary if terminal_status == "waiting_for_user" else None
            return {
                "ok": terminal_status in {"done", "waiting_for_user"},
                "status": terminal_status,
                "summary": summary or f"{task_name} completed.",
                "error": error,
                "needs_user_input": terminal_status == "waiting_for_user",
                "question": question,
                "wait_context": wait_context,
                "wait_timeout_sec": wait_timeout_sec,
                "current_description": f"{task_name}: {terminal_status}.",
                "model_alias": model_tier,
                "used_tool_access": tool_access,
                "used_skill_access": skill_access,
                "retryable_error": False,
                "attempts_used": 1,
                "attempts_configured": 1,
            }

        return {
            "ok": False,
            "status": "failed",
            "summary": summary,
            "error": error or "Agentic task execution failed.",
            "current_description": f"{task_name}: failed.",
            "model_alias": model_tier,
            "used_tool_access": tool_access,
            "used_skill_access": skill_access,
            "retryable_error": False,
            "attempts_used": 1,
            "attempts_configured": 1,
        }

    def run_profile(
        self,
        *,
        profile_id: str,
        payload: dict[str, Any] | None = None,
        cancel_event: Event | None = None,
        profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload_dict = payload if isinstance(payload, dict) else {}
        run_kind = str(payload_dict.get("run_kind") or "predefined").strip().lower()
        if run_kind in {"agentic", "interactive_wrapper"}:
            return self._run_agentic_task(payload=payload_dict, cancel_event=cancel_event)

        profile = profile if isinstance(profile, dict) else self._load_task_profiles().get(profile_id)
        if not isinstance(profile, dict):
            return {
                "ok": False,
                "status": "failed",
                "summary": None,
                "error": f"Task profile `{profile_id}` not found.",
                "current_description": f"Failed to resolve task profile `{profile_id}`.",
                "model_alias": "predefined",
                "used_tool_access": [],
                "used_skill_access": [],
                "retryable_error": False,
                "attempts_used": None,
                "attempts_configured": None,
            }

        profile_kind = str(profile.get("kind") or "script").strip().lower()
        if profile_kind in {"agentic", "interactive_wrapper"}:
            payload_agentic = {
                **payload_dict,
                "run_kind": "interactive_wrapper" if profile_kind == "interactive_wrapper" else "agentic",
                "task_name": str(profile.get("name") or profile_id),
            }
            return self._run_agentic_task(payload=payload_agentic, cancel_event=cancel_event)

        return self._run_predefined_task(
            task_id=profile_id,
            task_def=profile,
            payload=payload,
            cancel_event=cancel_event,
        )
