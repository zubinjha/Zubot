"""Daemon-first runtime entrypoint for local Zubot operation."""

from __future__ import annotations

import argparse
import signal
from threading import Event

from src.zubot.runtime.service import get_runtime_service


def _install_signal_handlers(stop_event: Event) -> None:
    def _handler(_sig, _frame) -> None:  # type: ignore[no-untyped-def]
        stop_event.set()

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


def run_daemon(
    *,
    with_app: bool = True,
    host: str = "127.0.0.1",
    port: int = 8000,
    tick_sec: float = 0.5,
    stop_event: Event | None = None,
) -> int:
    runtime = get_runtime_service()
    runtime.start(start_central_if_enabled=True, source="daemon")

    if with_app:
        try:
            import uvicorn
        except Exception as exc:  # pragma: no cover - dependency error guard
            runtime.stop(source="daemon")
            raise RuntimeError("uvicorn is required for daemon app mode") from exc

        try:
            uvicorn.run("app.main:app", host=host, port=port, reload=False)
        finally:
            runtime.stop(source="daemon")
        return 0

    signal_event = stop_event or Event()
    _install_signal_handlers(signal_event)
    try:
        while not signal_event.is_set():
            signal_event.wait(timeout=max(0.05, tick_sec))
    finally:
        runtime.stop(source="daemon")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Zubot daemon (runtime-first local mode).")
    parser.add_argument("--host", default="127.0.0.1", help="Local bind host for app mode.")
    parser.add_argument("--port", type=int, default=8000, help="Local bind port for app mode.")
    parser.add_argument(
        "--no-app",
        action="store_true",
        help="Run runtime daemon loop without launching local app server.",
    )
    parser.add_argument(
        "--tick-sec",
        type=float,
        default=0.5,
        help="Idle loop poll interval when running without app.",
    )
    args = parser.parse_args(argv)
    return run_daemon(
        with_app=not args.no_app,
        host=args.host,
        port=args.port,
        tick_sec=max(0.05, float(args.tick_sec)),
    )


if __name__ == "__main__":
    raise SystemExit(main())

