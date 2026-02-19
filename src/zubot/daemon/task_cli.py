"""Terminal task runner for direct task debugging."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
import signal
from threading import Event
from time import sleep
from pathlib import Path
from typing import Any

from src.zubot.core.config_loader import get_central_service_config, load_config
from src.zubot.core.task_agent_runner import TaskAgentRunner
from src.zubot.core.task_scheduler_store import TaskSchedulerStore, resolve_scheduler_db_path


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _db_path_from_config() -> Path:
    cfg = load_config()
    central = get_central_service_config(cfg)
    raw = central.get("scheduler_db_path")
    return resolve_scheduler_db_path(str(raw) if isinstance(raw, str) else None)


def _load_registered_profiles() -> dict[str, dict[str, Any]]:
    store = TaskSchedulerStore(db_path=_db_path_from_config())
    out: dict[str, dict[str, Any]] = {}
    for row in store.list_task_profiles():
        task_id = str(row.get("task_id") or "").strip()
        if task_id:
            out[task_id] = row
    return out


def _ensure_profile_registered(profile: dict[str, Any]) -> None:
    task_id = str(profile.get("task_id") or "").strip()
    if not task_id:
        return
    store = TaskSchedulerStore(db_path=_db_path_from_config())
    if store.get_task_profile(task_id=task_id):
        return
    store.upsert_task_profile(
        {
            "task_id": task_id,
            "name": str(profile.get("name") or task_id),
            "kind": str(profile.get("kind") or "script"),
            "entrypoint_path": profile.get("entrypoint_path"),
            "module": profile.get("module"),
            "resources_path": profile.get("resources_path"),
            "queue_group": profile.get("queue_group"),
            "timeout_sec": profile.get("timeout_sec"),
            "retry_policy": profile.get("retry_policy"),
            "enabled": bool(profile.get("enabled", True)),
            "source": str(profile.get("source") or "terminal_cli"),
        }
    )


def _discover_local_task_ids() -> list[str]:
    base = _repo_root() / "src" / "zubot" / "predefined_tasks"
    if not base.exists():
        return []
    out: list[str] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        if (child / "task.py").exists():
            out.append(child.name)
    return out


def _safe_payload(raw: str | None) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("--payload-json must decode to a JSON object.")
    return parsed


def _repo_relative(path: Path) -> str:
    return path.resolve().relative_to(_repo_root()).as_posix()


def _resolve_profile_definition(
    *,
    task_id: str,
    registered_profiles: dict[str, dict[str, Any]],
    explicit_entrypoint: str | None = None,
    explicit_resources: str | None = None,
) -> dict[str, Any] | None:
    profile = registered_profiles.get(task_id)
    if isinstance(profile, dict):
        return profile

    if explicit_entrypoint:
        entrypoint_path = Path(explicit_entrypoint).expanduser()
        if entrypoint_path.is_absolute():
            entrypoint_rel = _repo_relative(entrypoint_path)
            resources_rel = (
                _repo_relative(Path(explicit_resources).expanduser())
                if explicit_resources
                else _repo_relative(entrypoint_path.parent)
            )
        else:
            entrypoint_rel = entrypoint_path.as_posix()
            resources_rel = Path(explicit_resources).as_posix() if explicit_resources else entrypoint_path.parent.as_posix()
        return {
            "task_id": task_id,
            "name": task_id,
            "kind": "script",
            "entrypoint_path": entrypoint_rel,
            "resources_path": resources_rel,
            "enabled": True,
            "source": "terminal_cli",
        }

    default_entrypoint = _repo_root() / "src" / "zubot" / "predefined_tasks" / task_id / "task.py"
    if not default_entrypoint.exists():
        return None
    return {
        "task_id": task_id,
        "name": task_id,
        "kind": "script",
        "entrypoint_path": _repo_relative(default_entrypoint),
        "resources_path": _repo_relative(default_entrypoint.parent),
        "enabled": True,
        "source": "terminal_cli",
    }


def _print_profiles(profiles: dict[str, dict[str, Any]], local_ids: list[str]) -> None:
    print("Registered task profiles:")
    if not profiles:
        print("- (none)")
    else:
        for task_id in sorted(profiles):
            item = profiles[task_id]
            kind = str(item.get("kind") or "script")
            source = str(item.get("source") or "-")
            enabled = bool(item.get("enabled", True))
            print(f"- {task_id} kind={kind} enabled={enabled} source={source}")

    print("\nLocal predefined task folders with task.py:")
    if not local_ids:
        print("- (none)")
    else:
        for task_id in local_ids:
            print(f"- {task_id}")


def _cmd_list() -> int:
    profiles = _load_registered_profiles()
    local_ids = _discover_local_task_ids()
    _print_profiles(profiles, local_ids)
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    task_id = str(args.task_id or "").strip()
    if not task_id:
        print("error: task_id is required.")
        return 2
    try:
        payload = _safe_payload(args.payload_json)
    except Exception as exc:
        print(f"error: {exc}")
        return 2

    profiles = _load_registered_profiles()
    profile = _resolve_profile_definition(
        task_id=task_id,
        registered_profiles=profiles,
        explicit_entrypoint=args.entrypoint,
        explicit_resources=args.resources,
    )
    if not isinstance(profile, dict):
        print(f"error: task `{task_id}` not found in DB task_profiles and no local task.py fallback found.")
        return 1
    _ensure_profile_registered(profile)

    runner = TaskAgentRunner()
    print(f"[{_ts()}] Running task `{task_id}` from terminal...", flush=True)
    cancel_event = Event()
    old_sigint = signal.getsignal(signal.SIGINT)
    old_sigterm = signal.getsignal(signal.SIGTERM)
    signal_state = {"count": 0}

    def _request_cancel(_sig: int, _frame: Any) -> None:
        signal_state["count"] += 1
        if signal_state["count"] == 1:
            print(f"[{_ts()}] Cancel requested for `{task_id}`. Waiting for graceful stop...", flush=True)
            cancel_event.set()
            return
        raise KeyboardInterrupt

    old_tqdm = os.environ.get("ZUBOT_TASK_ENABLE_TQDM")
    old_stream = os.environ.get("ZUBOT_TASK_STREAM_STDOUT")
    os.environ["ZUBOT_TASK_ENABLE_TQDM"] = "1"
    os.environ["ZUBOT_TASK_STREAM_STDOUT"] = "1"
    signal.signal(signal.SIGINT, _request_cancel)
    signal.signal(signal.SIGTERM, _request_cancel)
    try:
        out = runner.run_profile(profile_id=task_id, payload=payload, profile=profile, cancel_event=cancel_event)
    except KeyboardInterrupt:
        cancel_event.set()
        print(f"[{_ts()}] Force stop requested for `{task_id}`.", flush=True)
        return 130
    finally:
        signal.signal(signal.SIGINT, old_sigint)
        signal.signal(signal.SIGTERM, old_sigterm)
        if old_tqdm is None:
            os.environ.pop("ZUBOT_TASK_ENABLE_TQDM", None)
        else:
            os.environ["ZUBOT_TASK_ENABLE_TQDM"] = old_tqdm
        if old_stream is None:
            os.environ.pop("ZUBOT_TASK_STREAM_STDOUT", None)
        else:
            os.environ["ZUBOT_TASK_STREAM_STDOUT"] = old_stream
    print(json.dumps(out, ensure_ascii=True, indent=2))
    return 0 if bool(out.get("ok")) else 1


def _find_local_task_processes(task_id: str) -> list[dict[str, Any]]:
    clean = str(task_id or "").strip()
    if not clean:
        return []
    patterns = (
        f"predefined_tasks/{clean}/task.py",
        f"predefined_tasks.{clean}.task",
    )
    me = os.getpid()
    out: list[dict[str, Any]] = []
    try:
        import subprocess

        raw = subprocess.check_output(["ps", "-axo", "pid,pgid,command"], text=True)
    except Exception:
        return out
    for line in raw.splitlines()[1:]:
        parts = line.strip().split(maxsplit=2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            pgid = int(parts[1])
        except Exception:
            continue
        cmd = parts[2]
        if pid == me:
            continue
        if any(token in cmd for token in patterns):
            out.append({"pid": pid, "pgid": pgid, "command": cmd})
    return out


def _cmd_stop(args: argparse.Namespace) -> int:
    task_id = str(args.task_id or "").strip()
    if not task_id:
        print("error: task_id is required.")
        return 2
    matches = _find_local_task_processes(task_id)
    if not matches:
        print(f"[{_ts()}] No local `{task_id}` task processes found.")
        return 0
    sig = signal.SIGKILL if bool(args.force) else signal.SIGTERM
    sig_name = "SIGKILL" if bool(args.force) else "SIGTERM"
    print(f"[{_ts()}] Stopping {len(matches)} local `{task_id}` process(es) with {sig_name}...", flush=True)
    for row in matches:
        pid = int(row["pid"])
        try:
            os.kill(pid, sig)
            print(f"- pid={pid} pgid={int(row['pgid'])} ok", flush=True)
        except ProcessLookupError:
            print(f"- pid={pid} already_exited", flush=True)
        except Exception as exc:
            print(f"- pid={pid} error={exc}", flush=True)
    sleep(0.15)
    survivors: list[int] = []
    for row in matches:
        pid = int(row["pid"])
        try:
            os.kill(pid, 0)
            survivors.append(pid)
        except Exception:
            continue
    if survivors and not bool(args.force):
        print(f"[{_ts()}] Some processes are still running: {survivors}. Re-run with --force.", flush=True)
        return 1
    if survivors:
        print(f"[{_ts()}] Could not stop all processes: {survivors}", flush=True)
        return 1
    print(f"[{_ts()}] Local `{task_id}` processes stopped.", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run or inspect Zubot tasks directly from terminal.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List registered task profiles and local predefined task folders.")

    run = sub.add_parser("run", help="Run one task now by task_id.")
    run.add_argument("task_id", help="Task profile id (for example: indeed_daily_search).")
    run.add_argument(
        "--payload-json",
        default="{}",
        help='JSON object payload passed into ZUBOT_TASK_PAYLOAD_JSON (default "{}").',
    )
    run.add_argument(
        "--entrypoint",
        default=None,
        help="Optional repo-relative or absolute entrypoint path to task.py for ad-hoc runs.",
    )
    run.add_argument(
        "--resources",
        default=None,
        help="Optional repo-relative or absolute resources folder path.",
    )

    stop = sub.add_parser("stop", help="Stop local task subprocesses by task_id.")
    stop.add_argument("task_id", help="Task id (for example: indeed_daily_search).")
    stop.add_argument(
        "--force",
        action="store_true",
        help="Use SIGKILL instead of SIGTERM.",
    )

    args = parser.parse_args(argv)
    if args.command == "list":
        return _cmd_list()
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "stop":
        return _cmd_stop(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
